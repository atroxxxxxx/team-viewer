package store

import (
	"context"
	"errors"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgtype"
	"github.com/jackc/pgx/v5/pgxpool"
)

func pgUUID(id uuid.UUID) pgtype.UUID {
	return pgtype.UUID{Bytes: id, Valid: true}
}

func goUUID(v pgtype.UUID) uuid.UUID {
	return v.Bytes
}

var ErrNotFound = errors.New("not found")
var ErrConflict = errors.New("conflict")

type Ticket struct {
	ShardID     int
	ID          uuid.UUID
	AccountID   uuid.UUID
	Description string
	Status      string
	Camera      bool
	Assignee    *uuid.UUID
	TunnelID    *uuid.UUID
	CreatedAt   time.Time
}

type Store struct {
	pool *pgxpool.Pool
}

func Open(ctx context.Context, dsn string) (*Store, error) {
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close() {
	s.pool.Close()
}

func (s *Store) Ping(ctx context.Context) error {
	return s.pool.Ping(ctx)
}

func (s *Store) LocalShardCount(ctx context.Context) (int, error) {
	var n int
	err := s.pool.QueryRow(ctx, `SELECT count(*) FROM shard_directory WHERE status = 'local'`).Scan(&n)
	return n, err
}

func (s *Store) CreateTicket(ctx context.Context, t Ticket, tokenHash string) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	_, err = tx.Exec(ctx, `
		INSERT INTO ticket_index (ticket_id, shard_id, account_id)
		VALUES ($1, $2, $3)`, pgUUID(t.ID), t.ShardID, pgUUID(t.AccountID))
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO tickets (shard_id, id, account_id, description, status, camera, token_hash)
		VALUES ($1, $2, $3, $4, $5, $6, $7)`,
		t.ShardID, pgUUID(t.ID), pgUUID(t.AccountID), t.Description, t.Status, t.Camera, tokenHash)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO chat_members (shard_id, ticket_id, member_id, role)
		VALUES ($1, $2, $3, 'client')`, t.ShardID, pgUUID(t.ID), t.AccountID.String())
	if err != nil {
		return err
	}
	if err := insertAction(ctx, tx, t.ShardID, pgUUID(t.ID), t.AccountID.String(), "create_ticket", "applied"); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) ListQueued(ctx context.Context, limit int) ([]Ticket, error) {
	rows, err := s.pool.Query(ctx, `
		SELECT shard_id, id, account_id, description, status, camera, assignee, tunnel_id, created_at
		FROM tickets
		WHERE status = 'queued'
		ORDER BY created_at
		LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	return collect(rows)
}

func (s *Store) TokenMatches(ctx context.Context, shardID int, ticketID uuid.UUID, hash string) (bool, error) {
	var one int
	err := s.pool.QueryRow(ctx, `
		SELECT 1 FROM tickets
		WHERE shard_id = $1 AND id = $2 AND token_hash = $3`,
		shardID, pgUUID(ticketID), hash).Scan(&one)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	return true, nil
}

func (s *Store) Locate(ctx context.Context, ticketID uuid.UUID) (int, error) {
	var shardID int
	err := s.pool.QueryRow(ctx, `SELECT shard_id FROM ticket_index WHERE ticket_id = $1`, pgUUID(ticketID)).Scan(&shardID)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, ErrNotFound
	}
	return shardID, err
}

func (s *Store) Get(ctx context.Context, shardID int, ticketID uuid.UUID) (Ticket, error) {
	var t Ticket
	var id, account, assignee, tunnel pgtype.UUID
	err := s.pool.QueryRow(ctx, `
		SELECT shard_id, id, account_id, description, status, camera, assignee, tunnel_id, created_at
		FROM tickets WHERE shard_id = $1 AND id = $2`, shardID, pgUUID(ticketID)).Scan(
		&t.ShardID, &id, &account, &t.Description, &t.Status, &t.Camera, &assignee, &tunnel, &t.CreatedAt)
	t.ID = goUUID(id)
	t.AccountID = goUUID(account)
	if assignee.Valid {
		value := goUUID(assignee)
		t.Assignee = &value
	}
	if tunnel.Valid {
		value := goUUID(tunnel)
		t.TunnelID = &value
	}
	if errors.Is(err, pgx.ErrNoRows) {
		return Ticket{}, ErrNotFound
	}
	return t, err
}

func (s *Store) Claim(ctx context.Context, shardID int, ticketID, operatorID, tunnelID uuid.UUID) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)

	tag, err := tx.Exec(ctx, `
		UPDATE tickets SET status = 'assigned', assignee = $3, tunnel_id = $4
		WHERE shard_id = $1 AND id = $2 AND status = 'queued'`,
		shardID, pgUUID(ticketID), pgUUID(operatorID), pgUUID(tunnelID))
	if err != nil {
		return err
	}
	if tag.RowsAffected() == 0 {
		return ErrConflict
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO chat_members (shard_id, ticket_id, member_id, role)
		VALUES ($1, $2, $3, 'operator')`, shardID, pgUUID(ticketID), operatorID.String())
	if err != nil {
		return err
	}
	if err := insertAction(ctx, tx, shardID, pgUUID(ticketID), operatorID.String(), "claim", "applied"); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func insertAction(ctx context.Context, tx pgx.Tx, shardID int, ticketID pgtype.UUID, actor, intent, phase string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO action_log (shard_id, id, ticket_id, actor, intent, phase)
		VALUES ($1, $2, $3, $4, $5, $6)`,
		shardID, pgUUID(uuid.New()), ticketID, actor, intent, phase)
	return err
}

func collect(rows pgx.Rows) ([]Ticket, error) {
	var out []Ticket
	for rows.Next() {
		var t Ticket
		var id, account, assignee, tunnel pgtype.UUID
		if err := rows.Scan(&t.ShardID, &id, &account, &t.Description, &t.Status, &t.Camera, &assignee, &tunnel, &t.CreatedAt); err != nil {
			return nil, err
		}
		t.ID = goUUID(id)
		t.AccountID = goUUID(account)
		if assignee.Valid {
			value := goUUID(assignee)
			t.Assignee = &value
		}
		if tunnel.Valid {
			value := goUUID(tunnel)
			t.TunnelID = &value
		}
		out = append(out, t)
	}
	return out, rows.Err()
}
