-- Секции по shard_id. Каталог shard_directory говорит, на каком узле лежит секция.
-- Сейчас все четыре секции локальные. Вынести секцию на другой узел: DETACH PARTITION
-- и запись dsn в каталоге. Ключ секции входит в первичный ключ каждой таблицы.

CREATE TABLE shard_directory (
    shard_id smallint PRIMARY KEY,
    node_name text NOT NULL,
    dsn text,
    status text NOT NULL CHECK (status IN ('local', 'remote', 'moving'))
);

INSERT INTO shard_directory (shard_id, node_name, dsn, status) VALUES
    (0, 'local', NULL, 'local'),
    (1, 'local', NULL, 'local'),
    (2, 'local', NULL, 'local'),
    (3, 'local', NULL, 'local');

-- Глобальный указатель id заявки -> секция. Сами заявки в нём не хранятся.
CREATE TABLE ticket_index (
    ticket_id uuid PRIMARY KEY,
    shard_id smallint NOT NULL REFERENCES shard_directory (shard_id),
    account_id uuid NOT NULL
);

CREATE OR REPLACE PROCEDURE attach_shards(parent regclass)
LANGUAGE plpgsql
AS $$
DECLARE
    i int;
    parent_name text;
BEGIN
    SELECT relname INTO parent_name FROM pg_class WHERE oid = parent;
    FOR i IN 0..3 LOOP
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF %I FOR VALUES IN (%s)',
            parent_name || '_s' || i,
            parent_name,
            i
        );
    END LOOP;
END;
$$;

CREATE TABLE tickets (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    description text NOT NULL,
    status text NOT NULL,
    camera boolean NOT NULL DEFAULT false,
    assignee uuid,
    token_hash text NOT NULL,
    tunnel_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('tickets'::regclass);
CREATE INDEX tickets_status_created_idx ON tickets (status, created_at DESC);

CREATE TABLE action_log (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    actor text NOT NULL,
    intent text NOT NULL,
    phase text NOT NULL,
    detail jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('action_log'::regclass);

CREATE TABLE problems (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    parent_step_id uuid,
    description text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('problems'::regclass);

CREATE TABLE plans (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    problem_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('plans'::regclass);

CREATE TABLE plan_steps (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    plan_id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    body text NOT NULL,
    status text NOT NULL,
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, plan_id) REFERENCES plans (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('plan_steps'::regclass);

CREATE TABLE observations (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    step_id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, step_id) REFERENCES plan_steps (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('observations'::regclass);

CREATE TABLE control_grants (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    status text NOT NULL,
    scope text,
    expires_at timestamptz,
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('control_grants'::regclass);

CREATE TABLE chat_messages (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    author text NOT NULL,
    body text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('chat_messages'::regclass);

CREATE TABLE chat_members (
    shard_id smallint NOT NULL,
    ticket_id uuid NOT NULL,
    member_id text NOT NULL,
    role text NOT NULL,
    left_at timestamptz,
    PRIMARY KEY (shard_id, ticket_id, member_id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('chat_members'::regclass);

CREATE TABLE confirmations (
    shard_id smallint NOT NULL,
    id uuid NOT NULL,
    ticket_id uuid NOT NULL,
    role text NOT NULL,
    accept boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (shard_id, id),
    FOREIGN KEY (shard_id, ticket_id) REFERENCES tickets (shard_id, id)
) PARTITION BY LIST (shard_id);
CALL attach_shards('confirmations'::regclass);
