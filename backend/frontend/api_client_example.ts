/** Reference only. No automatic retry, no local score/threshold calculation. */
import type {
  HealthResponse, JobsResponse, CandidateResponse, TargetJobResponse,
  JobSummaryResponse, MatchResponse, LearningPathResponse,
} from "./frontend_api_types";

declare global {
  interface ImportMetaEnv { readonly VITE_API_BASE: string }
  interface ImportMeta { readonly env: ImportMetaEnv }
}

const base = import.meta.env.VITE_API_BASE?.replace(/\/+$/, "");
if (!base) throw new Error("请配置 VITE_API_BASE 后重新启动 Vite");

export class BackendHttpError extends Error {
  constructor(public readonly status: number, public readonly body: unknown) {
    super(`Backend HTTP ${status}`);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // No short timeout or retry. A supplied signal is only for explicit user cancellation.
  const response = await fetch(`${base}${path}`, init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw new BackendHttpError(response.status, body);
  if (typeof body !== "object" || body === null) throw new Error("后端未返回 JSON 对象");
  return body as T; // compile-time wire types; not a replacement for backend validation
}

export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request("/health", { signal });
}
export function getJobs(q = "", limit = 30, signal?: AbortSignal): Promise<JobsResponse> {
  return request(`/api/jobs?${new URLSearchParams({ q, limit: String(limit) })}`, { signal });
}
let uploadInFlight = false;
export async function uploadCandidate(file: File, candidateId?: string, signal?: AbortSignal): Promise<CandidateResponse> {
  if (uploadInFlight) throw new Error("简历正在处理中，请勿重复提交");
  uploadInFlight = true;
  try {
    const form = new FormData();
    form.append("file", file);
    if (candidateId) form.append("candidate_id", candidateId);
    // Do not set multipart Content-Type: the browser supplies its boundary.
    return await request("/api/candidate", { method: "POST", body: form, signal });
  } finally { uploadInFlight = false; }
}
export function getTargetJob(jobId: string, jdKey?: string, signal?: AbortSignal): Promise<TargetJobResponse> {
  const query = jdKey ? `?${new URLSearchParams({ jd_key: jdKey })}` : "";
  return request(`/api/target-job/${encodeURIComponent(jobId)}${query}`, { signal });
}
export function getJobSummary(jobCode: string, signal?: AbortSignal): Promise<JobSummaryResponse> {
  return request(`/api/job-summary/${encodeURIComponent(jobCode)}`, { signal });
}
export function runMatch(candidate: CandidateResponse, target: TargetJobResponse, signal?: AbortSignal): Promise<MatchResponse> {
  return request("/api/match", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({candidate_profile: candidate.candidate_skill_profile,
      target_job_profile: target, auto_proficiency: true}),
  });
}
export function getLearningPath(candidate: CandidateResponse, match: MatchResponse, signal?: AbortSignal): Promise<LearningPathResponse> {
  return request("/api/learning-path", {
    method: "POST", headers: { "Content-Type": "application/json" }, signal,
    body: JSON.stringify({candidate_profile: candidate.candidate_skill_profile,
      target_job_profile: match.target_job_profile,
      proficiency_levels: match.proficiency.levels, auto_proficiency: false}),
  });
}
