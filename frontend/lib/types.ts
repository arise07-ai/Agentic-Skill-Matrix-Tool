export type Requirement = { skill: string; level: number; weight: number; min_people: number; known?: boolean; domain_tags?: string[] };

export type Seniority = { level: "Junior" | "Mid" | "Senior" | "Not assessed"; rated_skills: number; average: number | null; experienced_plus: number; mastered: number; reason: string };
export type SkillRecord = { raw_skill: string; rating: number | null };
export type ParsedRecord = { employee_no: string | null; name: string; roles: string[]; background: string; domain_tags: string[]; skills: SkillRecord[]; seniority?: Seniority };

export type UploadPreview = {
  upload_id: number; filename: string; kind: "excel" | "pdf"; source: string;
  sheets: string[]; sheet: string | null; columns: string[]; mapping: Record<string, string>; layout: "wide" | "long";
  records_count: number; skills_count: number; records: ParsedRecord[]; seniority_counts?: Record<string, number>; out_of_scale_warnings?: number; preview_rows: Record<string, string>[]; warnings: string[];
};

export type EmployeeRow = { id: number; employee_no: string | null; name: string; roles: string[]; skill_count: number; avg_rating: number | null; seniority?: Seniority };
export type EmployeeSkill = {
  raw_skill: string; canonical_skill: string; category: string; rating: number | null;
  method: string; confidence: number; needs_review: number;
};
export type EmployeeDetail = { id: number; employee_no: string | null; name: string; background: string; roles: string[]; skills: EmployeeSkill[]; domain_tags: string[]; seniority?: Seniority };

export type MatrixData = {
  skills: { name: string; category: string; holders: number }[];
  employees: EmployeeRow[];
  cells: Record<string, Record<string, number | null>>;
};

export type Dashboard = {
  employees: number; skills: number; needs_review: number; unrated: number; avg_rating: number | null;
  top_skills: { skill: string; category: string; holders: number; avg_rating: number | null }[];
  single_point_of_failure: { skill: string; category: string; holders: number }[];
  categories: { category: string; skills: number; avg_rating: number | null }[];
  rating_distribution: { rating: number; count: number }[];
};

export type MappingRow = {
  raw_skill: string; canonical_skill: string; category: string; method: string;
  confidence: number; needs_review: number; occurrences: number;
};
export type TaxonomyEntry = { name: string; category: string; aliases: string[] };

export type PersonBrief = { id: number; name: string; rating: number };
export type GapItem = {
  domain_tags?: string[];
  skill: string; level: number; min_people: number; weight: number; status: "covered" | "at_risk" | "gap";
  holders: PersonBrief[]; near: PersonBrief[]; best_rating: number; avg_rating: number | null;
  coverage: number; shortfall_people: number; risk: number;
  upskill: { id: number; name: string; rating: number; target: number; adjacent_skill: string | null; adjacency: number; score: number }[];
};
export type AiText = { text: string; source: "ai" } | null;
export type GapReport = {
  narrative?: AiText;
  items: GapItem[];
  summary: { readiness: number; covered: number; at_risk: number; gap: number; employees_considered: number };
};

export type Candidate = {
  seniority?: { level: string; reason: string };
  id: number; name: string; employee_no: string | null; roles: string[]; score: number; skill_score: number; semantic: number;
  matched: { skill: string; have: number; need: number; domain_tags?: string[]; via?: string }[];
  gaps: { skill: string; have: number; need: number; domain_tags?: string[]; via?: string }[]; rationale: string;
};
export type TeamMember = { role_matches?: string[]; seniority?: { level: string; reason: string }; id: number; name: string; employee_no: string | null; roles: string[]; score: number; role?: "contributor" | "extra"; meets: string[]; adds: string[]; below_target: string[]; rationale: string };
export type LearnerSkill = { domain_tags?: string[]; skill: string; target: number; current: number; score: number; basis: string; related_skill: string | null; related_rating: number | null };
export type LearningSuggestion = {
  employee: { id: number; name: string; employee_no: string | null; roles: string[] };
  skills: LearnerSkill[]; plan: LearningResult | null; error: string | null;
};
export type PositionCandidate = {
  seniority: { level: string; reason: string }; seniority_match: boolean | null;
  id: number; name: string; employee_no: string | null; roles: string[]; score: number; role_match: boolean; in_team: boolean; meets: number; total: number;
  gaps: { skill: string; have: number; need: number; domain_tags?: string[] }[]; short: boolean;
  learning_requirements: { skill: string; level: number; domain_tags?: string[] }[];
};
export type Position = { role: string; wanted_seniority: string | null; holders: number; candidates: PositionCandidate[]; ready_now: number; note: string | null };
export type StaffingResult = {
  positions?: Position[]; positions_hint?: string | null;
  summary?: AiText;
  candidates: Candidate[]; team: number[]; team_details?: TeamMember[]; team_size?: number; extras?: number[];
  uncovered: string[]; nobody?: string[]; team_limited?: string[]; learning?: LearningSuggestion[]; semantic_mode: string;
};

export type LearningResult = {
  markdown: string; mode: string; warnings: string[]; review_notes?: string[]; coach_notices?: string[];
  approval_required?: boolean; template_markdown?: string; computed_budget?: string; computed_schedule?: string[];
  gaps: { skill: string; current: number; target: number; delta: number; related_skill?: string; related_rating?: number }[];
};

export type RequestedSkill = { name: string; status: string; times_requested: number; last_context: string; last_seen: string; holders: number };

export type AiComponent = { key: string; label: string; configured: boolean; mode: string; env: string[]; file: string };
export type AiStatus = { ai_mode: "llm" | "heuristic"; components: AiComponent[] };

export type Role = "admin" | "project_manager" | "team_leader" | "hr_manager" | "member";
export type User = {
  id: number; username: string; display_name: string; role: Role; role_label: string; active: boolean; created_at?: string;
  employee_id?: number | null; team_leader_id?: number | null; employee_name?: string | null; team_leader_name?: string | null;
};
export type MemberAccount = User & { employee_name: string | null; team_leader_name: string | null };
export type TeamLeaderOption = { id: number; name: string; username: string };
export type MemberProfile = {
  user: User; employee: EmployeeDetail; team_leader: string | null; approver_label: string;
  skill_options: { name: string; category: string }[];
  learning_plans: { id: number; title: string; markdown: string; weeks: number | null; hours_per_week: number | null; mode: string; approved_by: string; created_at: string }[];
};
export type AuditRow = { id: number; ts: string; username: string; role: string; action: string; detail: Record<string, unknown> };

export type AgentKey = "standardization" | "intake" | "upload" | "chat";
export type AgentStep = { t: string; kind: "tool" | "info" | "question" | "answer"; text: string };
export type AgentRun = {
  id: string; agent: AgentKey; status: "running" | "waiting_input" | "done" | "error"; question: string | null; error: string | null;
  steps: AgentStep[] | null; result: { summary?: string; answer?: string; proposal_ids?: number[]; auto_applied?: number; employee_ids?: number[] } | null;
};
export type AgentsStatus = { available: boolean; message: string; web_lookup: boolean; agents: { key: AgentKey; title: string }[]; pending: { mine: number; all: number } };

export type ProposalKind = "skill_mapping" | "upload_fix" | "project_brief" | "employee_profile" | "employee_roles" | "member_update" | "member_cv" | "member_learning_plan" | "project_assignment" | "project_dates";
export type Proposal = {
  id: number; run_id: string; agent: string; kind: ProposalKind; title: string; payload: Record<string, any>; evidence: string;
  confidence: number | null; status: "pending" | "approved" | "rejected" | "auto_applied"; decided_by: string | null; decided_at: string | null;
  note: string; created_at: string; can_decide: boolean; requested_by?: number | null; requested_by_name?: string; approver?: string; capacity?: Capacity;
};
export type Brief = { id: number; title: string; prompt: string; requirements: Requirement[]; headcount: number | null; roles: string[]; notes: string; approved_by: string; created_at: string; start_date?: string | null; end_date?: string | null };

export type CvSkill = { raw_skill: string; rating: number; evidence: string; verified: boolean; needs_review: boolean; note: string };
export type CvPreview = {
  upload_id: number; filename: string; max_estimated_level: number;
  candidate: { name: string | null; employee_no: string | null; roles: string[]; background: string; skills: CvSkill[]; warnings: string[]; text_chars: number };
  seniority: Seniority; seniority_explanation: string; seniority_explanation_source: "ai" | "rules";
  existing_employee: { id: number | null; name: string; employee_no?: string | null; ambiguous?: boolean } | null;
};

export type RoleCount = { role: string; holders: number; variants: { spelling: string; count: number }[] };
export type RoleMatchJob = { id: string; status: "running" | "done" | "failed"; total: number; done: number; proposals: number; skipped_pending: number;
  dropped: number; errors: number; notes: string[]; error: string | null };

/** Project assignments: allocation is a percent of the 8-hour working day. */
export type CapacityProject = { assignment_id: number; brief_id: number; title: string; position: string; allocation_pct: number; hours: number; start?: string | null; end?: string | null };
export type Capacity = { allocated_pct: number; allocated_hours: number; free_pct: number; free_hours: number; pending_pct: number; free_after_pending_pct: number; window?: string; projects: CapacityProject[] };
export type CapacityMap = { day_hours: number; max_pct: number; employees: Record<string, Capacity> };
export type AssignedMember = { assignment_id: number; employee_id: number; name: string; position: string; allocation_pct: number; hours: number; other_pct: number; total_pct: number; since: string; approved_by: string; start_date: string | null; end_date: string | null; period_start: string | null; period_end: string | null };
export type TeamWaiting = { proposal_id: number; action: "assign" | "change" | "remove" | "dates"; employee_id: number | null; employee_name: string; position: string; allocation_pct: number | null; hours: number; period_start?: string | null; period_end?: string | null; requested_by_name?: string };
export type ProjectTeamData = { brief: { id: number; title: string; status: string; headcount: number | null; roles: string[]; start_date: string | null; end_date: string | null }; members: AssignedMember[]; waiting: TeamWaiting[]; day_hours: number; max_pct: number; total_pct: number; total_hours: number };

export type LearningCoachMessage = { role: "user" | "assistant"; content: string };
