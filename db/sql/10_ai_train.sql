-- ============================================================
-- 模块 AI · 数据闭环（训练语料 / 标注 / 模型 / RAG 知识库）
-- ============================================================
USE xiaojietong;

-- 训练语料（论坛/对话/通知回流，脱敏后）
DROP TABLE IF EXISTS `train_corpus`;
CREATE TABLE `train_corpus` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `source_type` VARCHAR(32)     NOT NULL COMMENT 'forum/chat/notice/qa',
    `source_id`   BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '来源记录 id（脱敏映射）',
    `content`     TEXT            NOT NULL COMMENT '语料内容（已脱敏）',
    `is_cleaned`  TINYINT         NOT NULL DEFAULT 0 COMMENT '是否已清洗',
    `is_deleted`  TINYINT         NOT NULL DEFAULT 0 COMMENT '软删除',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_source` (`source_type`),
    KEY `idx_cleaned` (`is_cleaned`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='AI 训练语料';

-- 指令标注（SFT 数据集）
DROP TABLE IF EXISTS `train_annotation`;
CREATE TABLE `train_annotation` (
    `id`         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `corpus_id`  BIGINT UNSIGNED NOT NULL COMMENT '语料 -> train_corpus.id',
    `instruction` TEXT           NOT NULL COMMENT '指令',
    `output`     TEXT            NOT NULL COMMENT '期望输出',
    `is_verified` TINYINT        NOT NULL DEFAULT 0 COMMENT '是否人工验证',
    `annotator`  VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '标注人',
    `created_at` DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_corpus` (`corpus_id`),
    KEY `idx_verified` (`is_verified`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='指令标注数据集';

-- 模型版本管理
DROP TABLE IF EXISTS `model_version`;
CREATE TABLE `model_version` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `name`         VARCHAR(64)     NOT NULL COMMENT '模型名，如 xjt-7b-v1',
    `base_model`   VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '基座，如 Qwen2.5-7B',
    `method`       VARCHAR(32)     NOT NULL DEFAULT 'QLoRA' COMMENT '微调方法',
    `quant_level`  VARCHAR(16)     NOT NULL DEFAULT 'q4_k_m' COMMENT '量化等级',
    `metrics_json` JSON            NULL COMMENT '评估指标 {acc,bleu,manual}',
    `status`       TINYINT         NOT NULL DEFAULT 0 COMMENT '0训练中 1可用 2已弃用',
    `file_path`    VARCHAR(255)    NOT NULL DEFAULT '' COMMENT 'GGUF 文件路径',
    `trained_at`   DATETIME        NULL DEFAULT NULL COMMENT '训练完成时间',
    `created_at`   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='模型版本';

-- RAG 知识库文档
DROP TABLE IF EXISTS `knowledge_doc`;
CREATE TABLE `knowledge_doc` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `title`       VARCHAR(255)    NOT NULL COMMENT '文档标题',
    `category`    VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '分类：校历/政策/图书馆/校医院/办事流程...',
    `content`     LONGTEXT        NULL COMMENT '原文',
    `source_url`  VARCHAR(255)    NOT NULL DEFAULT '' COMMENT '来源链接',
    `chunk_count` INT             NOT NULL DEFAULT 0 COMMENT '分块数',
    `status`      TINYINT         NOT NULL DEFAULT 0 COMMENT '0待向量化 1已入库 2停用',
    `updated_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_category` (`category`),
    KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='RAG 知识库文档';

-- 知识分块（对应向量库 chunk）
DROP TABLE IF EXISTS `knowledge_chunk`;
CREATE TABLE `knowledge_chunk` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `doc_id`      BIGINT UNSIGNED NOT NULL COMMENT '文档 -> knowledge_doc.id',
    `seq`         INT             NOT NULL DEFAULT 0 COMMENT '分块序号',
    `content`     TEXT            NOT NULL COMMENT '分块内容',
    `chunk_hash`  VARCHAR(64)     NOT NULL DEFAULT '' COMMENT '内容哈希（去重）',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_doc` (`doc_id`),
    UNIQUE KEY `uk_doc_seq` (`doc_id`, `seq`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='RAG 知识分块';

-- 图片资源（通用上传）
DROP TABLE IF EXISTS `image_asset`;
CREATE TABLE `image_asset` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键',
    `user_id`     BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '上传用户',
    `url`         VARCHAR(255)    NOT NULL COMMENT 'URL',
    `mime`        VARCHAR(32)     NOT NULL DEFAULT '' COMMENT '类型',
    `size_bytes`  INT             NOT NULL DEFAULT 0 COMMENT '大小',
    `md5`         VARCHAR(64)     NOT NULL DEFAULT '' COMMENT 'MD5',
    `created_at`  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='图片资源';
