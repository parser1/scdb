"""Action names shared by the audit log, the API, and the drift test."""

AUTH_DENIED = "auth.denied"
CLUSTER_CREATE = "cluster.create"
CLUSTER_DELETE = "cluster.delete"
NODE_RESTART = "node.restart"
NODE_REPLACE = "node.replace"
RECOVERY_FROZEN = "recovery.frozen"
BACKUP_START = "backup.start"
BACKUP_FINISH = "backup.finish"
RESTORE_START = "restore.start"
RESTORE_FINISH = "restore.finish"
OPS_DOWN = "ops.down"

AUDIT_ACTIONS = (
    AUTH_DENIED,
    CLUSTER_CREATE,
    CLUSTER_DELETE,
    NODE_RESTART,
    NODE_REPLACE,
    RECOVERY_FROZEN,
    BACKUP_START,
    BACKUP_FINISH,
    RESTORE_START,
    RESTORE_FINISH,
    OPS_DOWN,
)
