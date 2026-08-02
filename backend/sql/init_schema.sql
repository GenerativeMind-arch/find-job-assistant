-- ============================================================
-- Interview Assistant — 建库与建表（幂等，可重复执行）
-- 在 MySQL 8.0+ 服务器上执行
-- ============================================================

-- 1. 创建数据库（如不存在）
CREATE DATABASE IF NOT EXISTS interview_assistant
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

#interview_assistant
USE interview_assistant;

-- 2. 会话表
CREATE TABLE IF NOT EXISTS sessions (
    session_id  VARCHAR(64)  PRIMARY KEY,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    status      ENUM('active', 'archived') NOT NULL DEFAULT 'active',
    INDEX idx_sessions_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 3. JD 表
CREATE TABLE IF NOT EXISTS jobs (
    id          VARCHAR(64)  PRIMARY KEY,
    session_id  VARCHAR(64)  NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    data        JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_jobs_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 4. 候选人画像表
CREATE TABLE IF NOT EXISTS candidate_profiles (
    id          VARCHAR(64)  PRIMARY KEY,
    session_id  VARCHAR(64)  NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    data        JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_profiles_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 5. 简历内容表
CREATE TABLE IF NOT EXISTS resume_contents (
    id            VARCHAR(64)  PRIMARY KEY,
    session_id    VARCHAR(64)  NOT NULL,
    version       INT          NOT NULL DEFAULT 1,
    data          JSON         NOT NULL,
    content_hash  VARCHAR(64)  NOT NULL DEFAULT '',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_resume_contents_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 6. 渲染配置表
CREATE TABLE IF NOT EXISTS render_configs (
    id          VARCHAR(64)  PRIMARY KEY,
    session_id  VARCHAR(64)  NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    data        JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_render_configs_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 7. HTML 快照表
CREATE TABLE IF NOT EXISTS resume_htmls (
    id                           VARCHAR(64)  PRIMARY KEY,
    session_id                   VARCHAR(64)  NOT NULL,
    version                      INT          NOT NULL DEFAULT 1,
    html                         LONGTEXT     NOT NULL,
    derived_from_content_version INT          NOT NULL DEFAULT 1,
    derived_from_render_version  INT          NOT NULL DEFAULT 1,
    checksum                     VARCHAR(64)  NOT NULL DEFAULT '',
    created_at                   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_resume_htmls_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 8. 面试问答表
CREATE TABLE IF NOT EXISTS interview_qas (
    id          VARCHAR(64)  PRIMARY KEY,
    session_id  VARCHAR(64)  NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    data        JSON         NOT NULL,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_interview_qas_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 9. 事件流表
CREATE TABLE IF NOT EXISTS conversation_events (
    event_id           VARCHAR(64)  PRIMARY KEY,
    session_id         VARCHAR(64)  NOT NULL,
    message_id         VARCHAR(64)  NOT NULL,
    intent             VARCHAR(32)  NOT NULL,
    triggered_agents   JSON         NOT NULL,
    state_diff_summary JSON         DEFAULT NULL,
    status             ENUM('success', 'failed', 'partial') NOT NULL DEFAULT 'success',
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_events_session (session_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 9. Plan 执行记录表（Plan Mode 可观测性）
CREATE TABLE IF NOT EXISTS plan_runs (
    plan_id            VARCHAR(64)  PRIMARY KEY,
    session_id         VARCHAR(64)  NOT NULL,
    message_id         VARCHAR(64)  NOT NULL DEFAULT '',
    intent_bundle      JSON         NOT NULL,              -- {primary, secondary, entities}
    policy_json        JSON         DEFAULT NULL,           -- PlanPolicy JSON
    status             ENUM('running', 'success', 'partial', 'failed') NOT NULL DEFAULT 'running',
    total_latency_ms   DOUBLE       DEFAULT 0,
    error_summary      TEXT         DEFAULT NULL,
    started_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at           DATETIME     DEFAULT NULL,
    INDEX idx_planruns_session (session_id),
    INDEX idx_planruns_status (status),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 10. Step 执行记录表（Plan Mode 可观测性）
CREATE TABLE IF NOT EXISTS step_runs (
    step_run_id        VARCHAR(64)  PRIMARY KEY,
    plan_id            VARCHAR(64)  NOT NULL,
    step_id            VARCHAR(32)  NOT NULL,
    agent_name         VARCHAR(64)  NOT NULL,
    seq_index          INT          NOT NULL DEFAULT 0,    -- 执行顺序
    status             ENUM('pending', 'running', 'success', 'failed', 'skipped') NOT NULL DEFAULT 'pending',
    attempt            INT          NOT NULL DEFAULT 1,
    latency_ms         DOUBLE       DEFAULT 0,
    state_patch_summary JSON       DEFAULT NULL,           -- StatePatch 摘要
    error_code         VARCHAR(64)  DEFAULT NULL,
    error_message      TEXT         DEFAULT NULL,
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_stepruns_plan (plan_id),
    INDEX idx_stepruns_agent (agent_name),
    FOREIGN KEY (plan_id) REFERENCES plan_runs(plan_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 11. 岗位知识库 freshness 索引表
CREATE TABLE IF NOT EXISTS job_freshness_index (
    job_id      VARCHAR(64)  PRIMARY KEY,
    title       VARCHAR(255) NOT NULL,
    company     VARCHAR(255) DEFAULT '',
    city        VARCHAR(64)  DEFAULT '',
    source      VARCHAR(64)  DEFAULT 'boss-zhipin',
    crawled_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum    VARCHAR(64)  NOT NULL DEFAULT '',
    status      ENUM('active', 'removed') NOT NULL DEFAULT 'active',
    INDEX idx_jfi_crawled (crawled_at),
    INDEX idx_jfi_title (title)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
