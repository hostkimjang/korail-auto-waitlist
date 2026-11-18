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
