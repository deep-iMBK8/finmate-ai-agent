CREATE TABLE IF NOT EXISTS users (
  user_id VARCHAR(64) PRIMARY KEY,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS documents (
  document_id VARCHAR(255) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  original_filename VARCHAR(512),
  stored_path TEXT,
  txt_path TEXT,
  json_path TEXT,
  document_sector VARCHAR(64),
  document_date VARCHAR(32),
  document_type VARCHAR(255),
  company VARCHAR(255),
  document_title TEXT,
  status VARCHAR(32) NOT NULL DEFAULT 'processed',
  error_message TEXT,
  chunk_count INT NOT NULL DEFAULT 0,
  page_count INT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_documents_user_id (user_id),
  INDEX idx_documents_company (company),
  INDEX idx_documents_type (document_type),
  CONSTRAINT fk_documents_user FOREIGN KEY (user_id) REFERENCES users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS document_chunks (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  document_id VARCHAR(255) NOT NULL,
  chunk_id INT NOT NULL,
  page_number INT,
  chroma_id VARCHAR(512) NOT NULL,
  text_preview TEXT,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_document_chunk (document_id, chunk_id),
  INDEX idx_document_chunks_document_id (document_id),
  CONSTRAINT fk_document_chunks_document FOREIGN KEY (document_id) REFERENCES documents(document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  title VARCHAR(255),
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_chat_sessions_user_id (user_id),
  CONSTRAINT fk_chat_sessions_user FOREIGN KEY (user_id) REFERENCES users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS chat_messages (
  message_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  session_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  document_id VARCHAR(255),
  role VARCHAR(32) NOT NULL,
  content LONGTEXT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_chat_messages_session_id (session_id),
  INDEX idx_chat_messages_user_id (user_id),
  INDEX idx_chat_messages_document_id (document_id),
  CONSTRAINT fk_chat_messages_session FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id),
  CONSTRAINT fk_chat_messages_user FOREIGN KEY (user_id) REFERENCES users(user_id),
  CONSTRAINT fk_chat_messages_document FOREIGN KEY (document_id) REFERENCES documents(document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS retrieved_sources (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  message_id BIGINT NOT NULL,
  document_id VARCHAR(255),
  chunk_id INT,
  page_number INT,
  distance DOUBLE,
  metadata JSON,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_retrieved_sources_message_id (message_id),
  INDEX idx_retrieved_sources_document_id (document_id),
  CONSTRAINT fk_retrieved_sources_message FOREIGN KEY (message_id) REFERENCES chat_messages(message_id),
  CONSTRAINT fk_retrieved_sources_document FOREIGN KEY (document_id) REFERENCES documents(document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
