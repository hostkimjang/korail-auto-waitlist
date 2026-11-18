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

// RejectedError refuses a settings request before anything changed. Code is one of the
// E_SETTINGS_* job error codes the API and the web screen explain to the user.
type RejectedError struct{ Code string }

func (e RejectedError) Error() string { return "settings request rejected: " + e.Code }

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
