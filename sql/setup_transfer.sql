-- 创建文件传输专用库和账号
CREATE DATABASE IF NOT EXISTS file_transfer
  CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE TABLE IF NOT EXISTS file_transfer.files (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  path        VARCHAR(512) NOT NULL,
  content     LONGBLOB NOT NULL,
  size        INT NOT NULL,
  sha256      CHAR(64),
  created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uk_path (path(255))
) ENGINE=InnoDB;

-- 专用账号，只给 file_transfer 库权限
CREATE USER IF NOT EXISTS 'dsh_transfer'@'%' IDENTIFIED BY 'DsT#2026xfer!k9';
GRANT ALL PRIVILEGES ON file_transfer.* TO 'dsh_transfer'@'%';
FLUSH PRIVILEGES;

SELECT 'setup done' AS status;
