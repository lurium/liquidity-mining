/*
    PosgreSQL tables
*/

CREATE TABLE watcher_blocks (
    block_number INT,
    block_hash TEXT NOT NULL,
    PRIMARY KEY(block_number)
);

CREATE TABLE token_events (
    block_number INT,
    transaction_hash TEXT,
    event_index INT,
    token TEXT,
    holder TEXT,
    amount numeric(78,18) NOT NULL,   /* positive or negative */
    PRIMARY KEY(block_number, transaction_hash, event_index, token, holder)
);

CREATE MATERIALIZED VIEW token_balances AS
  SELECT
    token,
    holder,
    SUM(amount) AS balance
  FROM token_events
  GROUP BY token, holder;

CREATE UNIQUE INDEX idx_token_balances_token_holder
  ON token_balances(token, holder);

CREATE TABLE immature_mining_rewards (
    block_number INT,
    mining_round TEXT,
    holder TEXT,
    mcb_balance numeric(78,18) NOT NULL,
    PRIMARY KEY(block_number, mining_round, holder)
);

CREATE MATERIALIZED VIEW immature_mining_reward_summaries AS
  SELECT
    mining_round,
    holder,
    SUM(mcb_balance) AS mcb_balance
  FROM immature_mining_rewards
  GROUP BY mining_round, holder;

CREATE UNIQUE INDEX idx_immature_mining_reward_summaries_mining_round_holder
  ON immature_mining_reward_summaries (mining_round, holder);

CREATE TABLE mature_mining_rewards (
    mining_round TEXT,
    holder TEXT,
    mcb_balance numeric(78,18) NOT NULL,
    PRIMARY KEY(mining_round, holder)
);

CREATE TABLE payments (
    id SERIAL PRIMARY KEY,
    holder TEXT,
    amount numeric(78,18) NOT NULL,
    pay_time TIMESTAMP NOT NULL,
    trasaction_nonce INT NOT NULL,
    transaction_hash TEXT
);

CREATE MATERIALIZED VIEW payment_summaries AS
  SELECT
    holder,
    SUM(amount) AS paid_amount
  FROM payments
  GROUP BY holder;

CREATE TABLE round_payments (
    id SERIAL PRIMARY KEY,
    mining_round TEXT,
    holder TEXT,
    amount numeric(78,18) NOT NULL,
    payment_id INT,
    FOREIGN KEY (payment_id) REFERENCES payments (id)
);

CREATE MATERIALIZED VIEW round_payment_summaries AS
  SELECT
    mining_round,
    holder,
    SUM(amount) AS paid_amount
  FROM round_payments 
  GROUP BY mining_round, holder;