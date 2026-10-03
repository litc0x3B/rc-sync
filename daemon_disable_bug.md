```
.venv) ╭─litc@litc-nixos-pc ~/Nixos  ‹main*› 
╰─➤  rc-sync daemon disable 
[INFO] [manual] [daemon] Disabled and stopped rc-sync.timer.
(.venv) ╭─litc@litc-nixos-pc ~/Nixos  ‹main*› 
╰─➤  rc-sync daemon status  
=== Daemon Status (systemd) ===
× rc-sync.timer
     Loaded: not-found (Reason: Unit rc-sync.timer not found.)
     Active: failed (Result: resources)
 Invocation: da192baf03d84688bf667c4f67a5cd81
    Trigger: n/a

Oct 03 03:58:22 litc-nixos-pc systemd[1523]: Started rc-sync synchronization timer.
Oct 03 06:08:12 litc-nixos-pc systemd[1523]: rc-sync.timer: Unit to trigger vanished.
Oct 03 06:08:12 litc-nixos-pc systemd[1523]: rc-sync.timer: Failed with result 'resources'.

○ rc-sync.service - rc-sync synchronization service
     Loaded: loaded (/home/litc/.config/systemd/user/rc-sync.service; alias)
     Active: inactive (dead)

Oct 03 06:01:55 litc-nixos-pc rc-sync[70124]: [INFO] [systemd] [lock] Lock released.
Oct 03 06:01:55 litc-nixos-pc systemd[1523]: Finished rc-sync synchronization service.
Oct 03 06:05:52 litc-nixos-pc systemd[1523]: Starting rc-sync synchronization service...
Oct 03 06:05:52 litc-nixos-pc rc-sync[71979]: [INFO] [systemd] [lock] Lock acquired by PID 71979.
Oct 03 06:05:52 litc-nixos-pc rc-sync[71979]: [INFO] [systemd] [sync] Starting sync for 1 mapping(s)...
Oct 03 06:05:52 litc-nixos-pc rc-sync[71979]: [INFO] [systemd] [sync:sync] Running: rclone bisync /home/litc/Sync gd-vhivhi:save_files --resilient --recover --max-lock 3m
Oct 03 06:05:56 litc-nixos-pc rc-sync[71979]: [SUCCESS] [systemd] [sync:sync] Mapping completed successfully.
Oct 03 06:05:56 litc-nixos-pc rc-sync[71979]: [SUCCESS] [systemd] [sync] Synchronization batch finished (1 succeeded, 0 failed).
Oct 03 06:05:56 litc-nixos-pc rc-sync[71979]: [INFO] [systemd] [lock] Lock released.
Oct 03 06:05:56 litc-nixos-pc systemd[1523]: Finished rc-sync synchronization service.

=== Mappings Status ===
- [sync] (~/Sync <-> gd-vhivhi:save_files)
    Status:    SYNC_SUCCESS
    Last sync: 2026-10-03T06:05:56
    Enabled:   true
(.venv) ╭─litc@litc-nixos-pc ~/Nixos  ‹main*› 
╰─➤  rc-sync daemon enable =
Error: Got unexpected extra argument(s) (=)
(.venv) ╭─litc@litc-nixos-pc ~/Nixos  ‹main*› 
╰─➤  rc-sync daemon enable                                                                         1 ↵
[WARN] [manual] [daemon] Cannot overwrite systemd units (/home/litc/.config/systemd/user/rc-sync.service is read-only): proceeding with existing units.
[INFO] [manual] [daemon] Reloaded systemd user daemon.
[ERROR] [manual] [daemon] Failed to enable rc-sync.timer: Failed to enable unit: Unit rc-sync.timer does not exist
```
