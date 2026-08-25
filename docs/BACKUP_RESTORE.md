# 数据库备份与恢复

## 原则

- SQLite 命名卷不是备份；主机或数据卷损坏仍会导致数据丢失。
- `scripts/sqlite_backup.py` 使用 SQLite Online Backup API，可在应用运行时创建一致性备份。
- 每个备份完成后执行 `PRAGMA integrity_check`。
- 恢复前必须停止应用；恢复工具会先生成一份 `pre-restore` 安全副本。
- `backups/` 已从 Git 和 Docker 构建上下文排除，不应提交。

## Docker 数据库备份

```powershell
.\scripts\backup.ps1
```

备份写入宿主机 `backups/collab-时间戳.db`。完成后建议复制到异机或对象存储。

验证指定备份：

```powershell
python .\scripts\sqlite_backup.py check --database .\backups\collab-20260825T120000Z.db
```

## Docker 数据库恢复

先确认目标文件，然后执行：

```powershell
.\scripts\restore.ps1 -BackupFile .\backups\collab-20260825T120000Z.db -Yes
```

脚本将：

1. 检查备份完整性；
2. 停止应用容器；
3. 通过一次性容器恢复命名卷中的数据库；
4. 在覆盖前生成 `collab.db.pre-restore-时间戳.bak`；
5. 再次执行完整性检查；
6. 重新启动应用。

恢复后必须手工验证登录、项目列表、任务和审计日志。

## 本地数据库

```powershell
.\scripts\backup.ps1 -Local
.\scripts\restore.ps1 -Local -BackupFile .\backups\collab-20260825T120000Z.db -Yes
```

本地恢复前应停止 Uvicorn，避免恢复期间仍有写连接。

## 建议策略

- 每日备份，保留最近 14 天。
- 每周复制一份到异机或对象存储，保留至少 8 周。
- 每月至少进行一次独立恢复演练。
- 迁移、批量导入或版本升级前额外创建一次备份。

## PostgreSQL 备份与恢复

PostgreSQL overlay 使用自定义格式 `pg_dump`，并以 `pg_restore --list` 验证归档：

```powershell
.\scripts\backup_postgres.ps1
```

恢复会先创建一份新的安全备份、停止应用、执行 `pg_restore --clean --if-exists`，最后重启应用：

```powershell
.\scripts\restore_postgres.ps1 -BackupFile .\backups\collab-postgres-时间戳.dump -Yes
```

脚本只接受工作区 `backups/` 内的文件，避免误用任意路径。
