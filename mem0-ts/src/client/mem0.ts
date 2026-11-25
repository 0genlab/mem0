import axios from "axios";
import { v7 as uuidv7 } from "uuid";
import {
  AllUsers,
  PaginatedMemories,
  ProjectOptions,
  Memory,
  MemoryHistory,
  AddMemoryOptions,
  SearchMemoryOptions,
  GetAllMemoryOptions,
  DeleteAllMemoryOptions,
  DeleteMemoryOptions,
  MemoryUpdateBody,
  ProjectResponse,
  PromptUpdatePayload,
  Webhook,
  WebhookCreatePayload,
  WebhookUpdatePayload,
  Message,
  FeedbackPayload,
  CreateMemoryExportPayload,
  GetMemoryExportPayload,
  ProfileEntityType,
  ProfileResponse,
  ProfileJobResponse,
  ProfileJobStatus,
  ProfileSettings,
  ProfileSettingsResponse,
} from "./mem0.types";
import { camelToSnake, camelToSnakeKeys, snakeToCamelKeys } from "./utils";
import { createExceptionFromResponse, MemoryError } from "../common/exceptions";

// Entity params that must be passed via filters - check both snake_case and camelCase
const ENTITY_PARAMS = [
  "user_id",
  "agent_id",
  "app_id",
  "run_id",
  "userId",
  "agentId",
  "appId",
  "runId",
];

/**
 * Validates that no top-level entity parameters are passed.
 * @throws Error if entity params are found at top level
 */
function rejectTopLevelEntityParams(
  options: Record<string, any> | undefined,
  methodName: string,
): void {
  const invalidKeys = Object.keys(options ?? {}).filter((k) =>
    ENTITY_PARAMS.includes(k),
  );
  if (invalidKeys.length > 0) {
    throw new Error(
      `Top-level entity parameters [${invalidKeys.join(", ")}] are not supported in ${methodName}(). ` +
        `Use filters: { user_id: "..." } instead.`,
    );
  }
}

function rejectFilters(
  options: Record<string, any> | undefined,
  methodName: string,
): void {
  if (options?.filters != null) {
    throw new Error(
      `filters is not supported in ${methodName}(). ` +
        `Pass entity IDs at the top level, e.g. userId: "...".`,
    );
  }
}

/** Throws on an empty entity ID, which the API treats as matching every memory. */
function rejectBlankEntityIds(options: Record<string, any> | undefined): void {
  for (const [key, value] of Object.entries(options ?? {})) {
    if (
      ENTITY_PARAMS.includes(key) &&
      typeof value === "string" &&
      value.trim() === ""
    ) {
      throw new Error(
        `${key} cannot be empty or whitespace-only: an empty ID would match every memory in the project. ` +
          `To delete everything on purpose, call deleteAll() with "*" explicitly.`,
      );
    }
  }
}

function encodePathSegment(value: unknown): string {
  return encodeURIComponent(String(value));
}

class APIError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "APIError";
  }
}

interface ClientOptions {
  apiKey: string;
  host?: string;
  /** Max cached identities per process. Defaults to 50. */
  identityCacheMax?: number;
}

interface ClientIdentity {
  userId: string;
  resolved: boolean;
  organizationId: string | number | null;
  projectId: string | number | null;
}

// Shares one ping per (host, api key) across clients; FIFO-capped.
// One collection for every generation; the operation is a body field.
const PROFILE_JOBS_PATH = "/v2/profiles/jobs/";

const IDENTITY_CACHE_MAX_DEFAULT = 50;
const identityByCredentials = new Map<string, Promise<ClientIdentity>>();

declare const __MEM0_SDK_VERSION__: string | undefined;

// Injected by tsup (see mem0-ts/tsup.config.ts `define`). A hardcoded literal goes stale at the next release
// bump and then misreports the client version forever.
const SDK_VERSION =
  typeof __MEM0_SDK_VERSION__ !== "undefined" ? __MEM0_SDK_VERSION__ : "dev";

const MAX_STACK_ENTRIES = 4;
const MAX_STACK_CHARS = 200;

/**
 * Append our own entry and bound the result, dropping WHOLE entries.
 *
 * Neither cap cuts characters: slicing the joined string severs an identifier
 * and leaves a fragment the platform parses as a real client name. And the
 * reserved slot is ours. Pushing first and then trimming to four dropped exactly
 * the entry this exists to add whenever a caller already sent four, so we
 * vanished from our own stack while every caller claim survived.
 */
function boundedStack(callerEntries: string[], own: string): string {
  const kept: string[] = [];
  let budget = MAX_STACK_CHARS - own.length;
  for (const entry of callerEntries.slice(0, MAX_STACK_ENTRIES - 1)) {
    const cost = entry.length + ", ".length;
    if (cost > budget) break;
    budget -= cost;
    kept.push(entry);
  }
  return [...kept, own].join(", ");
}

/**
 * Surface-identity headers.
 *
 * X-Mem0-Source and X-Application are SET-ONCE by contract: whichever layer is
 * outermost sets them and nothing below overwrites, so a plugin wrapping this
 * SDK keeps its own identity. X-Mem0-Client is APPEND-ONLY - every layer adds
 * itself, so the platform sees the whole stack and not just the last speaker.
 */
function surfaceHeaders(): Record<string, string> {
  const env: Record<string, string | undefined> =
    typeof process !== "undefined" && process.env ? process.env : {};
  const existing = (env.MEM0_CLIENT_STACK ?? "").trim();
  const entries = existing
    ? existing
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
    : [];
  const headers: Record<string, string> = {
    "X-Mem0-Client": boundedStack(entries, `mem0-js/${SDK_VERSION}`),
  };
  const source = (env.MEM0_SOURCE ?? "").trim();
  if (source) headers["X-Mem0-Source"] = source;
  const application = (env.MEM0_APPLICATION ?? "").trim();
  if (application) headers["X-Application"] = application;
  return headers;
}

export default class MemoryClient {
  apiKey: string;
  host: string;
  private organizationId: string | number | null;
  private projectId: string | number | null;
  headers: Record<string, string>;
  client: any;
  userId: string;
  private initialized: Promise<void>;
  private identityCacheMax: number;

  _validateApiKey(): any {
    if (!this.apiKey) {
      throw new Error("Mem0 API key is required");
    }
    if (typeof this.apiKey !== "string") {
      throw new Error("Mem0 API key must be a string");
    }
    if (this.apiKey.trim() === "") {
      throw new Error("Mem0 API key cannot be empty");
    }
  }

  constructor(options: ClientOptions) {
    this.apiKey = options.apiKey;
    this.host = options.host || "https://api.mem0.ai";
    this.organizationId = null;
    this.projectId = null;
    this.identityCacheMax =
      options.identityCacheMax ?? IDENTITY_CACHE_MAX_DEFAULT;

    this.headers = {
      Authorization: `Token ${this.apiKey}`,
      "Content-Type": "application/json",
      ...surfaceHeaders(),
    };

    this.client = axios.create({
      baseURL: this.host,
      headers: { Authorization: `Token ${this.apiKey}` },
      timeout: 60000,
    });

    this._validateApiKey();

    this.userId = "";

    // Memory requests never wait on this; only _awaitIdentity does.
    this.initialized = this._resolveIdentity();
  }

  // One ping per credential pair per process, shared via identityByCredentials.
  private _resolveIdentity(): Promise<void> {
    const credentials = `${this.host}\u0000${this.apiKey}`;
    let shared = identityByCredentials.get(credentials);
    if (!shared) {
      shared = this._initializeClient();
      if (identityByCredentials.size >= this.identityCacheMax) {
        identityByCredentials.delete(
          identityByCredentials.keys().next().value!,
        );
      }
      identityByCredentials.set(credentials, shared);
      // A failed ping must not be cached, or the process never recovers.
      shared.then((identity) => {
        if (!identity.resolved) identityByCredentials.delete(credentials);
      });
    }
    return shared.then((identity) => {
      this.userId = identity.userId;
      if (identity.organizationId != null)
        this.organizationId = identity.organizationId;
      if (identity.projectId != null) this.projectId = identity.projectId;
    });
  }

  // Blocks until the ping has populated organizationId/projectId.
  private async _awaitIdentity(): Promise<void> {
    await this.initialized;
  }

  private async _initializeClient(): Promise<ClientIdentity> {
    let resolved = false;
    try {
      await this.ping();
      resolved = true;
    } catch (error: any) {
      console.error("Failed to initialize client:", error);
    }

    return {
      userId: this.userId,
      resolved,
      organizationId: this.organizationId,
      projectId: this.projectId,
    };
  }

  /** Fetch with no key conversion, for payloads carrying user-controlled property names. */
  async _fetchRawJson(url: string, options: any): Promise<any> {
    const response = await fetch(url, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Token ${this.apiKey}`,
        ...(this.userId ? { "Mem0-User-ID": this.userId } : {}),
      },
    });
    if (!response.ok) {
      const errorData = await response.text();
      throw createExceptionFromResponse(response.status, errorData);
    }
    return response.json();
  }

  async _fetchWithErrorHandling(url: string, options: any): Promise<any> {
    return snakeToCamelKeys(await this._fetchRawJson(url, options));
  }

  _preparePayload(
    messages: Array<Message>,
    options: Record<string, any>,
  ): object {
    const payload: any = {};
    payload.messages = messages;
    return camelToSnakeKeys({ ...payload, ...options });
  }

  _prepareParams(options: Record<string, any>): object {
    return Object.fromEntries(
      Object.entries(options).filter(([_, v]) => v != null),
    );
  }

  async ping(): Promise<void> {
    try {
      const response = await this._fetchWithErrorHandling(
        `${this.host}/v1/ping/`,
        {
          method: "GET",
          headers: {
            Authorization: `Token ${this.apiKey}`,
          },
        },
      );

      if (!response || typeof response !== "object") {
        throw new APIError("Invalid response format from ping endpoint");
      }

      if (response.status !== "ok") {
        throw new APIError(response.message || "API Key is invalid");
      }

      const { orgId, projectId, userEmail } = response;

      if (orgId) this.organizationId = orgId;
      if (projectId) this.projectId = projectId;
      if (userEmail) this.userId = userEmail;
    } catch (error: any) {
      // Pass through structured exceptions and APIError
      if (error instanceof MemoryError || error instanceof APIError) {
        throw error;
      } else {
        throw new APIError(
          `Failed to ping server: ${error.message || "Unknown error"}`,
        );
      }
    }
  }

  async add(
    messages: Array<Message>,
    options: AddMemoryOptions & Record<string, any> = {},
  ): Promise<Array<Memory>> {
    // Tightly scoped validation guard to resolve #5465
    if (!messages || (Array.isArray(messages) && messages.length === 0)) {
      throw new Error("Cannot process an empty messages payload.");
    }
    rejectFilters(options, "add");

    const payload = this._preparePayload(messages, options);
    const payloadKeys = Object.keys(payload);

    const response = await this._fetchWithErrorHandling(
      `${this.host}/v3/memories/add/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(payload),
      },
    );
    return response;
  }

  async update(
    memoryId: string,
    {
      text,
      metadata,
      timestamp,
      expirationDate,
    }: {
      text?: string;
      metadata?: Record<string, any>;
      timestamp?: number | string;
      expirationDate?: string | null;
    },
  ): Promise<Array<Memory>> {
    if (
      text === undefined &&
      metadata === undefined &&
      timestamp === undefined &&
      expirationDate === undefined
    ) {
      throw new Error(
        "At least one of text, metadata, timestamp, or expirationDate must be provided for update.",
      );
    }

    const payload: Record<string, any> = {};
    if (text !== undefined) payload.text = text;
    if (metadata !== undefined) payload.metadata = metadata;
    if (timestamp !== undefined) payload.timestamp = timestamp;
    if (expirationDate !== undefined) payload.expiration_date = expirationDate;

    const payloadKeys = Object.keys(payload);

    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/memories/${encodePathSegment(memoryId)}/`,
      {
        method: "PUT",
        headers: this.headers,
        body: JSON.stringify(payload),
      },
    );
    return response;
  }

  async get(memoryId: string): Promise<Memory> {
    return this._fetchWithErrorHandling(
      `${this.host}/v1/memories/${encodePathSegment(memoryId)}/`,
      {
        headers: this.headers,
      },
    );
  }

  async getAll(options?: GetAllMemoryOptions): Promise<PaginatedMemories> {
    // Reject top-level entity params - must use filters instead
    rejectTopLevelEntityParams(options as Record<string, any>, "getAll");

    const payloadKeys = Object.keys(options || {});
    const { page, pageSize, filters, ...rest } = options ?? {};
    const body: Record<string, any> = {
      ...camelToSnakeKeys(rest),
      ...(filters && { filters }),
    };

    const queryParams: string[] = [];
    if (page !== undefined) queryParams.push(`page=${page}`);
    if (pageSize !== undefined) queryParams.push(`page_size=${pageSize}`);
    const url = `${this.host}/v3/memories/${queryParams.length ? `?${queryParams.join("&")}` : ""}`;

    const response = await this._fetchWithErrorHandling(url, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify(body),
    });
    return response;
  }

  async search(
    query: string,
    options?: SearchMemoryOptions,
  ): Promise<{ results: Array<Memory> }> {
    // Reject top-level entity params - must use filters instead
    rejectTopLevelEntityParams(options as Record<string, any>, "search");

    const payloadKeys = Object.keys(options || {});
    const { filters, ...rest } = options ?? {};
    const payload: Record<string, any> = {
      query,
      output_format: "v1.1",
      ...camelToSnakeKeys(rest),
      ...(filters && { filters }),
    };

    const response = await this._fetchWithErrorHandling(
      `${this.host}/v3/memories/search/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(payload),
      },
    );
    return response;
  }

  async delete(
    memoryId: string,
    options: DeleteMemoryOptions = {},
  ): Promise<{ message: string }> {
    const snakeOptions = camelToSnakeKeys(this._prepareParams(options));
    // @ts-ignore
    const query = new URLSearchParams(snakeOptions).toString();
    return this._fetchWithErrorHandling(
      `${this.host}/v1/memories/${encodePathSegment(memoryId)}/${query ? `?${query}` : ""}`,
      {
        method: "DELETE",
        headers: this.headers,
      },
    );
  }

  async deleteAll(
    options: DeleteAllMemoryOptions = {},
  ): Promise<{ message: string }> {
    rejectFilters(options, "deleteAll");
    rejectBlankEntityIds(options);
    const payloadKeys = Object.keys(options || {});
    const snakeOptions = camelToSnakeKeys(this._prepareParams(options));
    // @ts-ignore
    const params = new URLSearchParams(snakeOptions);
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/memories/?${params}`,
      {
        method: "DELETE",
        headers: this.headers,
      },
    );
    return response;
  }

  async history(memoryId: string): Promise<Array<MemoryHistory>> {
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/memories/${encodePathSegment(memoryId)}/history/`,
      {
        headers: this.headers,
      },
    );
    return response;
  }

  async users(options?: {
    page?: number;
    pageSize?: number;
  }): Promise<AllUsers> {
    let url = `${this.host}/v1/entities/`;
    const params: string[] = [];
    if (options?.page) params.push(`page=${options.page}`);
    if (options?.pageSize) params.push(`page_size=${options.pageSize}`);
    if (params.length) url += `?${params.join("&")}`;
    const response = await this._fetchWithErrorHandling(url, {
      headers: this.headers,
    });
    return response;
  }

  /**
   * @deprecated The method should not be used, use `deleteUsers` instead. This will be removed in version 2.2.0.
   */
  async deleteUser(data: {
    entity_id: number;
    entity_type: string;
  }): Promise<{ message: string }> {
    if (!data.entity_type) {
      data.entity_type = "user";
    }
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/entities/${encodePathSegment(data.entity_type)}/${encodePathSegment(data.entity_id)}/`,
      {
        method: "DELETE",
        headers: this.headers,
      },
    );
    return response;
  }

  async deleteUsers(
    params: {
      userId?: string;
      agentId?: string;
      appId?: string;
      runId?: string;
    } = {},
  ): Promise<{ message: string }> {
    rejectBlankEntityIds(params);
    let to_delete: Array<{ type: string; name: string }> = [];
    const { userId, agentId, appId, runId } = params;

    if (userId) {
      to_delete = [{ type: "user", name: userId }];
    } else if (agentId) {
      to_delete = [{ type: "agent", name: agentId }];
    } else if (appId) {
      to_delete = [{ type: "app", name: appId }];
    } else if (runId) {
      to_delete = [{ type: "run", name: runId }];
    } else {
      const entities = await this.users();
      to_delete = entities.results.map((entity) => ({
        type: entity.type,
        name: entity.name,
      }));
    }

    if (to_delete.length === 0) {
      throw new Error("No entities to delete");
    }

    for (const entity of to_delete) {
      try {
        // fetch() reuses the pooled connection; axios here defaulted to
        // keepAlive: false, one handshake per entity.
        await this._fetchWithErrorHandling(
          `${this.host}/v2/entities/${encodePathSegment(entity.type)}/${encodePathSegment(entity.name)}/`,
          {
            method: "DELETE",
            headers: this.headers,
          },
        );
      } catch (error: any) {
        throw new APIError(
          `Failed to delete ${entity.type} ${entity.name}: ${error.message}`,
        );
      }
    }

    return {
      message:
        userId || agentId || appId || runId
          ? "Entity deleted successfully."
          : "All users, agents, apps and runs deleted.",
    };
  }

  async batchUpdate(memories: Array<MemoryUpdateBody>): Promise<string> {
    const memoriesBody = memories.map((memory) => ({
      memory_id: memory.memoryId,
      text: memory.text,
    }));
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/batch/`,
      {
        method: "PUT",
        headers: this.headers,
        body: JSON.stringify({ memories: memoriesBody }),
      },
    );
    return response;
  }

  async batchDelete(memories: Array<string>): Promise<string> {
    const memoriesBody = memories.map((memory) => ({
      memory_id: memory,
    }));
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/batch/`,
      {
        method: "DELETE",
        headers: this.headers,
        body: JSON.stringify({ memories: memoriesBody }),
      },
    );
    return response;
  }

  async getProject(options: ProjectOptions): Promise<ProjectResponse> {
    const payloadKeys = Object.keys(options || {});
    const { fields } = options;
    await this._awaitIdentity();

    if (!(this.organizationId && this.projectId)) {
      throw new Error(
        "organizationId and projectId must be set to access instructions or categories",
      );
    }

    const params = new URLSearchParams();
    fields?.forEach((field) => params.append("fields", camelToSnake(field)));

    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/orgs/organizations/${this.organizationId}/projects/${this.projectId}/?${params.toString()}`,
      {
        headers: this.headers,
      },
    );
    return response;
  }

  async updateProject(
    prompts: PromptUpdatePayload,
  ): Promise<Record<string, any>> {
    await this._awaitIdentity();
    if (!(this.organizationId && this.projectId)) {
      throw new Error(
        "organizationId and projectId must be set to update instructions or categories",
      );
    }

    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/orgs/organizations/${this.organizationId}/projects/${this.projectId}/`,
      {
        method: "PATCH",
        headers: this.headers,
        body: JSON.stringify(camelToSnakeKeys(prompts)),
      },
    );
    return response;
  }

  // WebHooks
  async getWebhooks(data?: { projectId?: string }): Promise<Array<Webhook>> {
    if (!data?.projectId) await this._awaitIdentity();
    const project_id = data?.projectId || this.projectId;
    if (!project_id) {
      throw new Error("projectId must be set to access webhooks");
    }
    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/webhooks/projects/${project_id}/`,
      {
        headers: this.headers,
      },
    );
    return response;
  }

  async createWebhook(webhook: WebhookCreatePayload): Promise<Webhook> {
    await this._awaitIdentity();
    if (!this.projectId) {
      throw new Error("projectId must be set to create a webhook");
    }
    const body = {
      name: webhook.name,
      url: webhook.url,
      event_types: webhook.eventTypes,
    };
    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/webhooks/projects/${this.projectId}/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(body),
      },
    );
    return response;
  }

  async updateWebhook(
    webhook: WebhookUpdatePayload,
  ): Promise<{ message: string }> {
    const body: Record<string, any> = {};
    if (webhook.name != null) body.name = webhook.name;
    if (webhook.url != null) body.url = webhook.url;
    if (webhook.eventTypes != null) body.event_types = webhook.eventTypes;
    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/webhooks/${webhook.webhookId}/`,
      {
        method: "PUT",
        headers: this.headers,
        body: JSON.stringify(body),
      },
    );
    return response;
  }

  async deleteWebhook(data: {
    webhookId: string;
  }): Promise<{ message: string }> {
    const webhook_id = data.webhookId || data;
    const response = await this._fetchWithErrorHandling(
      `${this.host}/api/v1/webhooks/${webhook_id}/`,
      {
        method: "DELETE",
        headers: this.headers,
      },
    );
    return response;
  }

  async feedback(data: FeedbackPayload): Promise<{ message: string }> {
    const payloadKeys = Object.keys(data || {});
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/feedback/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify(camelToSnakeKeys(data)),
      },
    );
    return response;
  }

  /**
   * Get the memory profile for a single user.
   *
   * Branch on `status`, not on an empty `profile`: generation is asynchronous,
   * so a known user without a profile yet is a normal response.
   */
  async getProfile(data: { entityId: string }): Promise<ProfileResponse> {
    await this._awaitIdentity();

    const response = await this._fetchWithErrorHandling(
      `${this.host}/v2/entities/user/${encodeURIComponent(data.entityId)}/profile/`,
      {
        headers: this.headers,
      },
    );
    return response;
  }

  /**
   * Generate or refresh the profile for one user, now.
   *
   * Profiles are otherwise built once a user crosses an internal message
   * threshold, so a new user has none for its first few memories. Returns as
   * soon as the work is queued: poll {@link getProfile} and branch on `status`.
   *
   * Pass `idempotencyKey` and reuse it to retry a lost request without starting
   * (and being billed for) a second job.
   */
  async generateProfile(data: {
    entityId: string;
    idempotencyKey?: string;
  }): Promise<ProfileJobResponse> {
    await this._awaitIdentity();

    const response = await this._fetchWithErrorHandling(
      `${this.host}${PROFILE_JOBS_PATH}`,
      {
        method: "POST",
        headers: {
          ...this.headers,
          "Idempotency-Key": data.idempotencyKey ?? uuidv7(),
        },
        body: JSON.stringify({
          operation: "trigger",
          entity_type: "user",
          entity_id: data.entityId,
        }),
      },
    );
    return response;
  }

  /** Get the profile settings for the current project. */
  async getProfileSettings(): Promise<ProfileSettingsResponse> {
    await this._awaitIdentity();

    const raw = await this._fetchRawJson(`${this.host}/v2/profiles/settings/`, {
      headers: this.headers,
    });
    return this._settingsWithVerbatimSchema(raw);
  }

  /**
   * The envelope keys are ours; the schema's property names are the customer's.
   *
   * Each entity type carries its own schema, so every one has to be restored
   * from the raw body — otherwise camel-casing rewrites the customer's field
   * names and a profile comes back under keys they never chose.
   */
  private _settingsWithVerbatimSchema(raw: any): ProfileSettingsResponse {
    const settings = snakeToCamelKeys(raw) as ProfileSettingsResponse;
    if (!raw || typeof raw !== "object") {
      return settings;
    }

    const rawEntities = raw.entities;
    if (rawEntities && typeof rawEntities === "object") {
      for (const [entityType, entitySettings] of Object.entries(rawEntities)) {
        if (
          entitySettings &&
          typeof entitySettings === "object" &&
          "schema" in entitySettings &&
          settings.entities?.[entityType as ProfileEntityType]
        ) {
          settings.entities[entityType as ProfileEntityType]!.schema = (
            entitySettings as Record<string, any>
          ).schema;
        }
      }
    }

    return settings;
  }

  /**
   * Update profile settings. Only the fields you pass are written.
   *
   * `schema` and `customInstructions` are per user and are nested under
   * `entities` for the API; only `enabled` is project-wide. Sending them flat
   * is rejected with `Unsupported settings`.
   */
  async updateProfileSettings(
    settings: ProfileSettings,
  ): Promise<ProfileSettingsResponse> {
    const payloadKeys = Object.keys(settings || {});
    await this._awaitIdentity();

    const { schema, customInstructions, enabled } = settings || {};

    const body: Record<string, any> = {};
    if (enabled !== undefined) {
      body.enabled = enabled;
    }

    const entitySettings: Record<string, any> = {};
    // The schema's property names are the customer's and must reach the API verbatim.
    if (schema !== undefined) {
      entitySettings.schema = schema;
    }
    if (customInstructions !== undefined) {
      entitySettings.custom_instructions = customInstructions;
    }
    if (Object.keys(entitySettings).length > 0) {
      body.entities = { user: entitySettings };
    }

    const raw = await this._fetchRawJson(`${this.host}/v2/profiles/settings/`, {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify(body),
    });
    return this._settingsWithVerbatimSchema(raw);
  }

  /**
   * Generate profiles for a few real users, to check a schema.
   *
   * Real generations against real memories, and the results are kept: the
   * profiles are written to those users and count toward usage.
   */
  async sampleProfiles(data?: {
    limit?: number;
    idempotencyKey?: string;
  }): Promise<ProfileJobResponse> {
    await this._awaitIdentity();

    const response = await this._fetchWithErrorHandling(
      `${this.host}${PROFILE_JOBS_PATH}`,
      {
        method: "POST",
        headers: {
          ...this.headers,
          "Idempotency-Key": data?.idempotencyKey ?? uuidv7(),
        },
        body: JSON.stringify({
          operation: "sample",
          // Required: the API refuses a job that does not name an entity kind.
          entity_type: "user",
          ...this._prepareParams({ limit: data?.limit }),
        }),
      },
    );
    return response;
  }

  /**
   * Read one generation job. Accepts the `statusUrl` from a create call, or a
   * bare job id. Prefer `statusUrl` so a route change needs no client update.
   */
  async getProfileJob(jobIdOrStatusUrl: string): Promise<ProfileJobStatus> {
    await this._awaitIdentity();

    const path = jobIdOrStatusUrl.startsWith("/")
      ? jobIdOrStatusUrl
      : `${PROFILE_JOBS_PATH}${jobIdOrStatusUrl}/`;
    return this._fetchWithErrorHandling(`${this.host}${path}`, {
      method: "GET",
      headers: this.headers,
    });
  }

  async createMemoryExport(
    data: CreateMemoryExportPayload,
  ): Promise<{ message: string; id: string }> {
    if (!data.filters || !data.schema) {
      throw new Error("Missing filters or schema");
    }

    // filters and schema are user-controlled blobs whose keys must reach the
    // API verbatim; only the remaining SDK params (e.g. exportInstructions)
    // get camel->snake conversion. See issue #5593.
    const { filters, schema, ...rest } = data;
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/exports/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify({
          ...camelToSnakeKeys(rest),
          filters,
          schema,
        }),
      },
    );

    return response;
  }

  async getMemoryExport(
    data: GetMemoryExportPayload,
  ): Promise<{ message: string; id: string }> {
    if (!data.memoryExportId && !data.filters) {
      throw new Error("Missing memoryExportId or filters");
    }

    const { filters, ...rest } = data;
    const response = await this._fetchWithErrorHandling(
      `${this.host}/v1/exports/get/`,
      {
        method: "POST",
        headers: this.headers,
        body: JSON.stringify({
          ...camelToSnakeKeys(rest),
          ...(filters && { filters }),
        }),
      },
    );
    return response;
  }
}

export { MemoryClient };
