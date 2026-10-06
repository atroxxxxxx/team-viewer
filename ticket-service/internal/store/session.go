package store

import (
	"context"
	"encoding/json"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
)

type ChatMessage struct {
	ID        uuid.UUID `json:"id"`
	Author    string    `json:"author"`
	Body      string    `json:"body"`
	CreatedAt time.Time `json:"created_at"`
}

type PlanStep struct {
	ID     uuid.UUID `json:"id"`
	PlanID uuid.UUID `json:"plan_id"`
	Body   string    `json:"body"`
	Status string    `json:"status"`
}

type ControlGrant struct {
	ID        uuid.UUID
	Status    string
	Scope     string
	ExpiresAt *time.Time
}

type LogItem struct {
	ID        uuid.UUID       `json:"id"`
	Actor     string          `json:"actor"`
	Intent    string          `json:"intent"`
	Phase     string          `json:"phase"`
	Detail    json.RawMessage `json:"detail"`
	CreatedAt time.Time       `json:"created_at"`
}

type Confirmation struct {
	Role      string    `json:"role"`
	Accept    bool      `json:"accept"`
	CreatedAt time.Time `json:"created_at"`
}

func (s *Store) AddMessage(ctx context.Context, shardID int, ticketID uuid.UUID, author, body string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	_, err = tx.Exec(ctx, `
		INSERT INTO chat_messages (shard_id, id, ticket_id, author, body)
		VALUES ($1, $2, $3, $4, $5)`,
		shardID, pgUUID(uuid.New()), pgUUID(ticketID), author, body)
	if err != nil {
		return err
	}
	if err := insertAction(ctx, tx, shardID, pgUUID(ticketID), author, "chat", "applied"); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) Messages(ctx context.Context, shardID int, ticketID uuid.UUID) ([]ChatMessage, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, author, body, created_at
		FROM chat_messages WHERE shard_id = $1 AND ticket_id = $2
		ORDER BY created_at`, shardID, pgUUID(ticketID))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []ChatMessage
	for rows.Next() {
		var item ChatMessage
		var id pgtype.UUID
		if err := rows.Scan(&id, &item.Author, &item.Body, &item.CreatedAt); err != nil {
			return nil, err
		}
		item.ID = goUUID(id)
		out = append(out, item)
	}
	return out, rows.Err()
}

func (s *Store) RequestControl(ctx context.Context, shardID int, ticketID uuid.UUID, intent string) (uuid.UUID, error) {
	grantID := uuid.New()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return uuid.UUID{}, err
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `
		UPDATE tickets SET status = 'awaiting_permission'
		WHERE shard_id = $1 AND id = $2 AND status IN ('assigned', 'guided', 'remote_control')`,
		shardID, pgUUID(ticketID))
	if err != nil {
		return uuid.UUID{}, err
	}
	if tag.RowsAffected() == 0 {
		return uuid.UUID{}, ErrConflict
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO control_grants (shard_id, id, ticket_id, status, scope)
		VALUES ($1, $2, $3, 'pending', '')`,
		shardID, pgUUID(grantID), pgUUID(ticketID))
	if err != nil {
		return uuid.UUID{}, err
	}
	if err := insertAction(ctx, tx, shardID, pgUUID(ticketID), "operator", intent, "proposed"); err != nil {
		return uuid.UUID{}, err
	}
	return grantID, tx.Commit(ctx)
}

func (s *Store) DecideControl(ctx context.Context, shardID int, ticketID, grantID uuid.UUID, allow bool, scope string, ttl time.Duration) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	status := "denied"
	ticketStatus := "guided"
	if allow {
		status = "active"
		ticketStatus = "remote_control"
	}
	tag, err := tx.Exec(ctx, `
		UPDATE control_grants
		SET status = $4, scope = $5, expires_at = CASE WHEN $6 THEN now() + ($7 * interval '1 second') ELSE NULL END
		WHERE shard_id = $1 AND id = $2 AND ticket_id = $3 AND status = 'pending'`,
		shardID, pgUUID(grantID), pgUUID(ticketID), status, scope, allow, int(ttl.Seconds()))
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrConflict
	}
	if _, err := tx.Exec(ctx, `
		UPDATE tickets SET status = $3 WHERE shard_id = $1 AND id = $2`,
		shardID, pgUUID(ticketID), ticketStatus); err != nil {
		return err
	}
	phase := "rejected_by_client"
	if allow {
		phase = "applied"
	}
	if err := insertAction(ctx, tx, shardID, pgUUID(ticketID), "client", "control_decision", phase); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) ActiveGrant(ctx context.Context, shardID int, ticketID uuid.UUID) (ControlGrant, error) {
	var grant ControlGrant
	var id pgtype.UUID
	var scope *string
	err := s.pool.QueryRow(ctx, `
		SELECT id, status, scope, expires_at
		FROM control_grants
		WHERE shard_id = $1 AND ticket_id = $2
		ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END
		LIMIT 1`, shardID, pgUUID(ticketID)).Scan(&id, &grant.Status, &scope, &grant.ExpiresAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return ControlGrant{}, ErrNotFound
	}
	if err != nil {
		return ControlGrant{}, err
	}
	grant.ID = goUUID(id)
	if scope != nil {
		grant.Scope = *scope
	}
	return grant, nil
}

func (s *Store) ProposeInput(ctx context.Context, shardID int, ticketID, grantID uuid.UUID, intent string, detail []byte) (uuid.UUID, error) {
	grant, err := s.ActiveGrant(ctx, shardID, ticketID)
	if err != nil {
		return uuid.UUID{}, err
	}
	if grant.ID != grantID || grant.Status != "active" || (grant.ExpiresAt != nil && grant.ExpiresAt.Before(time.Now())) {
		return uuid.UUID{}, ErrConflict
	}
	actionID := uuid.New()
	_, err = s.pool.Exec(ctx, `
		INSERT INTO action_log (shard_id, id, ticket_id, actor, intent, phase, detail)
		VALUES ($1, $2, $3, 'operator', $4, 'proposed', $5::jsonb)`,
		shardID, pgUUID(actionID), pgUUID(ticketID), intent, string(detail))
	return actionID, err
}

func (s *Store) FinishInput(ctx context.Context, shardID int, ticketID, actionID uuid.UUID, phase, observation string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	tag, err := tx.Exec(ctx, `
		UPDATE action_log SET phase = $4
		WHERE shard_id = $1 AND id = $2 AND ticket_id = $3 AND phase = 'proposed'`,
		shardID, pgUUID(actionID), pgUUID(ticketID), phase)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	if observation != "" {
		_, err = tx.Exec(ctx, `
			INSERT INTO chat_messages (shard_id, id, ticket_id, author, body)
			VALUES ($1, $2, $3, 'client', $4)`,
			shardID, pgUUID(uuid.New()), pgUUID(ticketID), observation)
		if err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (s *Store) LogBlocked(ctx context.Context, shardID int, ticketID uuid.UUID, actor, intent string, detail []byte) error {
	_, err := s.pool.Exec(ctx, `
		INSERT INTO action_log (shard_id, id, ticket_id, actor, intent, phase, detail)
		VALUES ($1, $2, $3, $4, $5, 'blocked_by_policy', $6::jsonb)`,
		shardID, pgUUID(uuid.New()), pgUUID(ticketID), actor, intent, string(detail))
	return err
}

func (s *Store) CreatePlan(ctx context.Context, shardID int, ticketID uuid.UUID, problemID *uuid.UUID, steps []string) (uuid.UUID, error) {
	planID := uuid.New()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return uuid.UUID{}, err
	}
	defer tx.Rollback(ctx)
	var problem pgtype.UUID
	if problemID != nil {
		problem = pgUUID(*problemID)
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO plans (shard_id, id, ticket_id, problem_id)
		VALUES ($1, $2, $3, $4)`,
		shardID, pgUUID(planID), pgUUID(ticketID), problem)
	if err != nil {
		return uuid.UUID{}, err
	}
	for _, body := range steps {
		_, err = tx.Exec(ctx, `
			INSERT INTO plan_steps (shard_id, id, plan_id, ticket_id, body, status)
			VALUES ($1, $2, $3, $4, $5, 'todo')`,
			shardID, pgUUID(uuid.New()), pgUUID(planID), pgUUID(ticketID), body)
		if err != nil {
			return uuid.UUID{}, err
		}
	}
	_, err = tx.Exec(ctx, `
		UPDATE tickets SET status = 'guided'
		WHERE shard_id = $1 AND id = $2 AND status <> 'remote_control'`,
		shardID, pgUUID(ticketID))
	if err != nil {
		return uuid.UUID{}, err
	}
	if err := insertAction(ctx, tx, shardID, pgUUID(ticketID), "operator", "create_plan", "applied"); err != nil {
		return uuid.UUID{}, err
	}
	return planID, tx.Commit(ctx)
}

func (s *Store) Steps(ctx context.Context, shardID int, ticketID uuid.UUID) ([]PlanStep, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, plan_id, body, status
		FROM plan_steps WHERE shard_id = $1 AND ticket_id = $2
		ORDER BY body`, shardID, pgUUID(ticketID))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []PlanStep
	for rows.Next() {
		var step PlanStep
		var id, planID pgtype.UUID
		if err := rows.Scan(&id, &planID, &step.Body, &step.Status); err != nil {
			return nil, err
		}
		step.ID = goUUID(id)
		step.PlanID = goUUID(planID)
		out = append(out, step)
	}
	return out, rows.Err()
}

func (s *Store) AddObservation(ctx context.Context, shardID int, ticketID, stepID uuid.UUID, body string) error {
	tag, err := s.pool.Exec(ctx, `
		INSERT INTO observations (shard_id, id, step_id, ticket_id, body)
		SELECT $1, $2, id, ticket_id, $5 FROM plan_steps
		WHERE shard_id = $1 AND id = $3 AND ticket_id = $4`,
		shardID, pgUUID(uuid.New()), pgUUID(stepID), pgUUID(ticketID), body)
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrNotFound
	}
	return nil
}

func (s *Store) CompleteStep(ctx context.Context, shardID int, ticketID, stepID uuid.UUID, seenOnScreen, body string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if seenOnScreen == "true" {
		if err := s.AddObservation(ctx, shardID, ticketID, stepID, "вижу на экране: "+body); err != nil {
			return err
		}
	}
	var n int
	err = tx.QueryRow(ctx, `
		SELECT count(*) FROM observations WHERE shard_id = $1 AND step_id = $2 AND ticket_id = $3`,
		shardID, pgUUID(stepID), pgUUID(ticketID)).Scan(&n)
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrConflict
	}
	tag, err := tx.Exec(ctx, `
		UPDATE plan_steps SET status = 'done'
		WHERE shard_id = $1 AND id = $2 AND ticket_id = $3 AND status = 'todo'`,
		shardID, pgUUID(stepID), pgUUID(ticketID))
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrConflict
	}
	return tx.Commit(ctx)
}

func (s *Store) ReviseStep(ctx context.Context, shardID int, ticketID, stepID uuid.UUID, body string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	var planID pgtype.UUID
	err = tx.QueryRow(ctx, `
		UPDATE plan_steps SET status = 'cancelled'
		WHERE shard_id = $1 AND id = $2 AND ticket_id = $3 AND status = 'todo'
		RETURNING plan_id`,
		shardID, pgUUID(stepID), pgUUID(ticketID)).Scan(&planID)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrConflict
	}
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO plan_steps (shard_id, id, plan_id, ticket_id, body, status)
		VALUES ($1, $2, $3, $4, $5, 'todo')`,
		shardID, pgUUID(uuid.New()), planID, pgUUID(ticketID), body)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) AddProblem(ctx context.Context, shardID int, ticketID, parentStep uuid.UUID, description string, steps []string) (uuid.UUID, error) {
	problemID := uuid.New()
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return uuid.UUID{}, err
	}
	defer tx.Rollback(ctx)
	_, err = tx.Exec(ctx, `
		INSERT INTO problems (shard_id, id, ticket_id, parent_step_id, description)
		VALUES ($1, $2, $3, $4, $5)`,
		shardID, pgUUID(problemID), pgUUID(ticketID), pgUUID(parentStep), description)
	if err != nil {
		return uuid.UUID{}, err
	}
	tag, err := tx.Exec(ctx, `
		UPDATE plan_steps SET status = 'blocked'
		WHERE shard_id = $1 AND id = $2 AND ticket_id = $3`,
		shardID, pgUUID(parentStep), pgUUID(ticketID))
	if err != nil {
		return uuid.UUID{}, err
	}
	if tag.RowsAffected() == 0 {
		return uuid.UUID{}, ErrNotFound
	}
	if err := tx.Commit(ctx); err != nil {
		return uuid.UUID{}, err
	}
	if _, err := s.CreatePlan(ctx, shardID, ticketID, &problemID, steps); err != nil {
		return uuid.UUID{}, err
	}
	return problemID, nil
}

func (s *Store) Confirm(ctx context.Context, shardID int, ticketID uuid.UUID, role string, accept bool) (string, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer tx.Rollback(ctx)
	_, err = tx.Exec(ctx, `
		INSERT INTO confirmations (shard_id, id, ticket_id, role, accept)
		VALUES ($1, $2, $3, $4, $5)`,
		shardID, pgUUID(uuid.New()), pgUUID(ticketID), role, accept)
	if err != nil {
		return "", err
	}
	if !accept {
		status := "guided"
		grant, err := s.ActiveGrant(ctx, shardID, ticketID)
		if err == nil && grant.Status == "active" && (grant.ExpiresAt == nil || grant.ExpiresAt.After(time.Now())) {
			status = "remote_control"
		}
		if _, err := tx.Exec(ctx, `UPDATE tickets SET status = $3 WHERE shard_id = $1 AND id = $2`, shardID, pgUUID(ticketID), status); err != nil {
			return "", err
		}
		return status, tx.Commit(ctx)
	}
	var open int
	if err := tx.QueryRow(ctx, `
		SELECT count(*) FROM plan_steps
		WHERE shard_id = $1 AND ticket_id = $2 AND status IN ('todo', 'blocked')`,
		shardID, pgUUID(ticketID)).Scan(&open); err != nil {
		return "", err
	}
	if open > 0 {
		return "", ErrConflict
	}
	var operatorYes, clientYes bool
	_ = tx.QueryRow(ctx, `
		SELECT accept FROM confirmations
		WHERE shard_id = $1 AND ticket_id = $2 AND role = 'operator'
		ORDER BY created_at DESC LIMIT 1`, shardID, pgUUID(ticketID)).Scan(&operatorYes)
	_ = tx.QueryRow(ctx, `
		SELECT accept FROM confirmations
		WHERE shard_id = $1 AND ticket_id = $2 AND role = 'client'
		ORDER BY created_at DESC LIMIT 1`, shardID, pgUUID(ticketID)).Scan(&clientYes)
	status := "awaiting_client"
	if operatorYes && clientYes {
		status = "resolved"
		if _, err := tx.Exec(ctx, `
			UPDATE media_grants SET revoked_at = now()
			WHERE shard_id = $1 AND ticket_id = $2 AND revoked_at IS NULL`,
			shardID, pgUUID(ticketID)); err != nil {
			return "", err
		}
		if _, err := tx.Exec(ctx, `
			UPDATE chat_members SET left_at = now()
			WHERE shard_id = $1 AND ticket_id = $2 AND role = 'operator' AND left_at IS NULL`,
			shardID, pgUUID(ticketID)); err != nil {
			return "", err
		}
	}
	if _, err := tx.Exec(ctx, `UPDATE tickets SET status = $3 WHERE shard_id = $1 AND id = $2`, shardID, pgUUID(ticketID), status); err != nil {
		return "", err
	}
	return status, tx.Commit(ctx)
}

func (s *Store) PendingInputs(ctx context.Context, shardID int, ticketID uuid.UUID) ([]LogItem, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT id, actor, intent, phase, detail, created_at
		FROM action_log
		WHERE shard_id = $1 AND ticket_id = $2 AND phase = 'proposed'
		  AND detail->>'type' IN ('pointer', 'key', 'text')
		ORDER BY created_at`, shardID, pgUUID(ticketID))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collectLogs(rows)
}

func (s *Store) Confirmations(ctx context.Context, shardID int, ticketID uuid.UUID) ([]Confirmation, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT role, accept, created_at FROM confirmations
		WHERE shard_id = $1 AND ticket_id = $2
		ORDER BY created_at`, shardID, pgUUID(ticketID))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []Confirmation
	for rows.Next() {
		var item Confirmation
		if err := rows.Scan(&item.Role, &item.Accept, &item.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

func collectLogs(rows pgx.Rows) ([]LogItem, error) {
	var out []LogItem
	for rows.Next() {
		var item LogItem
		var id pgtype.UUID
		if err := rows.Scan(&id, &item.Actor, &item.Intent, &item.Phase, &item.Detail, &item.CreatedAt); err != nil {
			return nil, err
		}
		item.ID = goUUID(id)
		out = append(out, item)
	}
	return out, rows.Err()
}
