// Package manager supervises durable update requests outside the application stack.
package manager

import "context"

type Request struct {
	Schema          int    `json:"schema"`
	ID              string `json:"id"`
	TargetReleaseID string `json:"target_release_id"`
	PlanID          string `json:"plan_id"`
	IdempotencyKey  string `json:"idempotency_key"`
	AuthToken       string `json:"auth_token"`
}

type Job struct {
	Schema          int    `json:"schema"`
	ID              string `json:"id"`
	TargetReleaseID string `json:"target_release_id"`
	PlanID          string `json:"plan_id"`
	IdempotencyKey  string `json:"idempotency_key"`
	State           string `json:"state"`
	Stage           string `json:"stage"`
	CreatedAt       string `json:"created_at"`
	UpdatedAt       string `json:"updated_at"`
	CurrentVersion  string `json:"current_version,omitempty"`
	TargetVersion   string `json:"target_version,omitempty"`
	ErrorCode       string `json:"error_code,omitempty"`
	DuplicateOf     string `json:"duplicate_of,omitempty"`
}

type Result struct{ State, Stage, CurrentVersion, TargetVersion string }
type Progress func(stage string) error
type Handler func(context.Context, Request, Progress) (Result, error)

// SettingsRequest asks the manager to change .env settings from the web settings window.
// The API writes it as requests/<id>.settings.json. Managers before 1.4.8 accept only
// <id>.json names, so they leave it alone and the API offers the window only when this
// manager's settings snapshot is present. Exactly one of Fields and Raw is set.
type SettingsRequest struct {
	Schema         int               `json:"schema"`
	ID             string            `json:"id"`
	IdempotencyKey string            `json:"idempotency_key"`
	AuthToken      string            `json:"auth_token"`
	BaseRevision   string            `json:"base_revision"`
	Fields         map[string]string `json:"fields,omitempty"`
	Raw            *string           `json:"raw,omitempty"`
}

// SettingsPlanner saves a plan for an authenticated settings request and returns the
// ordinary request that applies it. It returns RejectedError when nothing may change.
type SettingsPlanner func(context.Context, SettingsRequest) (Request, error)

// RestartRequest asks the manager to restart the app services with the current .env.
// The API writes it as requests/<id>.restart.json. Earlier managers accept only <id>.json
// and <id>.settings.json names, so they leave it alone; the API offers a restart only when
// this manager's capabilities.json lists "restart". Scope is always "app".
type RestartRequest struct {
	Schema         int    `json:"schema"`
	ID             string `json:"id"`
	IdempotencyKey string `json:"idempotency_key"`
	AuthToken      string `json:"auth_token"`
	Scope          string `json:"scope"`
}

// RestartPlanner saves a restart plan for an authenticated restart request and returns the
// ordinary request that runs it. It returns RejectedError when nothing may restart.
type RestartPlanner func(context.Context, RestartRequest) (Request, error)

// RejectedError refuses a settings or restart request before anything changed. Code is one
// of the E_SETTINGS_* or E_RESTART_* job error codes the API and the web screen explain.
type RejectedError struct{ Code string }

func (e RejectedError) Error() string { return "manager request rejected: " + e.Code }

// SettingsField reports one setting the web form edits. Value is omitted for a secret,
// which only reports whether it is set.
type SettingsField struct {
	Value      *string `json:"value,omitempty"`
	Configured bool    `json:"configured"`
}

// SettingsSnapshot is the secret-free view of .env published as status/settings.json.
// ManagerStartedAt matches the heartbeat of the manager that wrote it, so the API can
// tell a snapshot left behind by an earlier manager process from a current one.
type SettingsSnapshot struct {
	Schema           int                      `json:"schema"`
	ManagerStartedAt string                   `json:"manager_started_at"`
	GeneratedAt      string                   `json:"generated_at"`
	Revision         string                   `json:"revision"`
	Fields           map[string]SettingsField `json:"fields"`
	Raw              string                   `json:"raw"`
	SecretKeys       []string                 `json:"secret_keys"`
	LockedKeys       []string                 `json:"locked_keys"`
}

type Heartbeat struct {
	Schema      int    `json:"schema"`
	State       string `json:"state"`
	PID         int    `json:"pid"`
	StartedAt   string `json:"started_at"`
	HeartbeatAt string `json:"heartbeat_at"`
}

// Capabilities is status/capabilities.json: what the running manager can do for the API.
// ManagerStartedAt is exactly the started_at of the heartbeat of the manager that wrote it,
// so the API trusts the file only while that manager is the one running.
type Capabilities struct {
	Schema           int      `json:"schema"`
	ManagerStartedAt string   `json:"manager_started_at"`
	Version          string   `json:"version"`
	Capabilities     []string `json:"capabilities"`
	InstallPath      string   `json:"install_path"`
	HostOS           string   `json:"host_os"`
}

type Catalog struct {
	Schema         int    `json:"schema"`
	CheckedAt      string `json:"checked_at"`
	CurrentVersion string `json:"current_version"`
	LatestVersion  string `json:"latest_version"`
	PlanID         string `json:"plan_id"`
	Available      bool   `json:"available"`
	Reason         string `json:"reason"`
}

type Paths struct{ Root, Auth, Control, Gate, Requests, Status, Jobs string }
