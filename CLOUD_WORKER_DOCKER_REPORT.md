# Cloud worker Docker store: investigation record

- **Host:** cloud worker `iZt4ndd2dff7gqjta7mppaZ` (Jenkins node `linux-iZt4ndd2dff7gqjta7mppaZ`)
- **Prepared by:** Toby. Findings from 14 and 15 Oct 2026; investigation on 15 Oct 2026.
- **For:** the mentor and the root account holder.
- **Scope:** finding the cause only. Nothing is repaired, removed or restarted during the investigation. The repair plan will be written after the mentor and root have reviewed the result.
- **This document is the record:**
  - what was found (sections 1 to 3);
  - the access used (section 4);
  - the questions (section 5);
  - what was done, and when (section 6);
  - the answers (section 7);
  - where each piece of evidence is (Appendix B).
- **The commands are in a separate guide:** [`CLOUD_WORKER_INVESTIGATION_STEPS.md`](CLOUD_WORKER_INVESTIGATION_STEPS.md). It has every step, with its purpose, risk, copy-paste command and expected result.
- **Status (15 Oct, 11:10):** guide step 0 (securing `root.pem`) is next.

## Summary

- The shared image store (containerd, in `/data/containerd`) is damaged for images pulled before 12 Oct. All 113 DeepSWE images and 7 of 11 common images fail; images pulled since then work.
- Most of the damage has a clear cause: a chain of events around the restart on 12 Oct.
  1. From 11 Oct 15:21, the host ran out of memory again and again until 12 Oct 10:36. The system journal was killed at 15:23 and stopped recording. At 10:38 on 12 Oct the host was restarted without a clean shutdown: both disks had to recover their filesystem journals.
  2. The `/data` line in `/etc/fstab` had literal double quotes around it: `"/dev/nvme1n1p1 /data ext4 defaults 0 0"`. That isn't valid, so at the 10:38 boot the line was skipped and `/data` wasn't mounted.
  3. containerd started anyway, at 10:38 and again at 10:40, on the empty `/data` folder of the system disk. It created a new, empty database there.
  4. At 10:54, `/data` was mounted while containerd was still running. containerd kept its empty database, but its files and its cleanup now went to the real disk. The cleanup removes layer folders its database doesn't list, so it removed the real store's layer folders.
- The fstab trigger was fixed at 12:09 on 12 Oct: the line was rewritten correctly, and containerd and Docker were made to require `/data`.
- **Not explained yet:**
  - downloaded files missing across many old images;
  - stale records seen on 6 and 7 Oct, before the restart.
  
  Three candidates remain: a clean-up that deleted old files by age, files deleted by hand, or files lost in the forced restart. Today's investigation is designed to tell them apart (section 5).
- A DeepSWE image's downloaded files are still on disk; one image exported in full. Only its unpacked layer folders are missing.

**Today's plan** (guide steps 0 to 8): about 2 hours of commands, plus a test that runs 2 to 4 hours in the background. The result is ready for the mentor at the end of the day.

| Guide step | What | Time |
|---|---|---|
| 0 | Secure `root.pem` on Toby's PC, and test it | 5 min |
| 1 | Install the investigation-only sudo rule, with `root.pem`, after root's OK | 10 min |
| 2 | Log in as `toby`, check access, start recording, save the two scripts | 5 min |
| 3 | Copy the logs, and start the long jobs: two log scans and the image start test | 15 min, then 2 to 4 hours in the background |
| 4 | The logs: boots, memory, logins, 12 and 14 Oct | 30 min |
| 5 | containerd's records against the disk | 20 min |
| 6 | What only root can read: keys, history, crontabs, the hidden store, two containers | 20 min |
| 7 | Results of the long jobs | 15 min |
| 8 | Finish; fill in sections 6 and 7 | 30 min |

None of the commands is high risk, except opening the root shell in step 1.1, which is used only to install the rule. The only low-risk actions are a temporary read-only view of the system disk, and test containers that start and are removed at once.

## 1. What was checked

As `toby`, without sudo:

1. Host facts: versions, settings, disk and permissions.
2. Eleven common images: pulled if missing, then started once with no network.
3. All 113 DeepSWE images at the benchmark commit mac-k3d pins (`0b9fabbb`): started once with no network, without pulling.
4. Possible causes, from these sources:
   - systemd's timestamps;
   - `/etc/fstab` and its backup;
   - the mount table and container creation times;
   - scheduled jobs, other Docker daemons, filesystem error counters, log sizes and the package history;
   - on 15 Oct, the boot list, the kernel log and the syslog around the 12 Oct restart.

Host facts:

- Ubuntu 26.04 LTS, kernel 7.0.0-22, 96 CPUs, 182 GB of memory. Docker 29.6.2 with containerd 2.2.6 and the containerd image store.
- The store holds 7,089 images and 497 containers, 43 of them running. `live_restore` is false, so restarting Docker stops every container. Restarting containerd does not; it was restarted twice on 14 Oct with all containers still running.
- containerd keeps its data in `/data/containerd`. `/data` is a separate 14 TB ext4 disk, 88% used, with 1.7 TB free. The system disk is 66% used, with 165 GB free.
- `toby` is in the `docker` group. Since 15 Oct he is also in `adm` and `systemd-journal`, so he can read the system journal and `/var/log` without sudo. He has no sudo yet, and his password is locked. containerd's socket is root-only (`root:root`, mode 0660).
- `sudo` on this host is `sudo-rs` 0.2.13, Ubuntu's default. Password logins over SSH are off for every account (`/etc/ssh/sshd_config.d/00-disable-password.conf`). Root may log in with a key only (`PermitRootLogin prohibit-password`).
- **How far back the logs go:**
  - The journal is capped at 3.9 GB. It still lists the boots of 24 Jul, 11 Sep and 12 Oct, but containerd's entries start on 10 Oct at 02:08.
  - `/var/log/syslog*`, `auth.log*` and `kern.log*` start on 13 Sep.
  - Nothing older than 13 Sep can be checked from the logs.
  - These files rotate weekly, keeping four weeks. At the next rotation, on Sunday 18 Oct, the week of 13 to 20 Sep is deleted. Guide step 3.1 copies the logs first.

## 2. Findings

### 2.1 DeepSWE: none of the 113 images starts

All 113 images are on the host, and all 113 fail to start for the same reason: folders that hold their layers are missing.

```text
failed to stat parent: stat /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/154281/fs: no such file or directory
failed to mount ... lowerdir=.../snapshots/154273/fs:...:.../snapshots/153120/fs ... err: no such file or directory
```

All 113 images are built on the same 23 base layers (folders 153120 to 153142).

The images were pulled between 10 and 29 Sep. `docs/testing/question-log.md` records two runs with them on this worker:

- 8 Oct: 40 questions run and scored (`deepswe_some_task` #9).
- 9 Oct: three more passed (`deepswe_one_task` #93 and `deepswe_some_task` #11).

Their downloaded files appear to be intact. One of them (`abs-module-cache-flags`) exported in full with `docker save`: 840 MB, with no error. That export reads every downloaded file of the image. So for DeepSWE, only the unpacked layer folders are missing.

### 2.2 Common images: 7 of 11 fail

| Image | Used for | Result |
|---|---|---|
| `ghcr.io/astral-sh/uv:0.9.30-python3.13-bookworm` | Building the JiuwenSwarm bundle | Fails: layer record already exists (`sha256:51e78f04…`) |
| `alpine:3.23.4` | Harbor's egress probe | Fails: layer record already exists (`sha256:29df493b…`), first seen on 6 Oct (build #53) |
| `alpine:3.20` | mac-k3d's fallback for deleting files that containers left owned by root | Fails: parent layer missing (`sha256:08bc4e…`), seen on 7 Oct (build #61) |
| `alpine:3.19@sha256:6baf4358…` | mac-k3d's last egress-probe fallback | Fails: downloaded file missing |
| `debian:bookworm-slim` | Common base image | Fails: downloaded file missing |
| `ubuntu:22.04` | Common base image | Fails: downloaded file missing |
| `ubuntu:24.04` | Common base image | Fails: downloaded file missing |
| `alpine:3.21`, `alpine:3.22`, `busybox:1.36`, `debian:bookworm` | (control images) | Work |

"Downloaded file missing" is this error: `blob sha256:… expected at /data/containerd/io.containerd.content.v1.content/blobs/sha256/…: blob not found`. containerd's database lists the file, but the file is not on disk.

All four images that work were pulled on 14 Oct. `alpine:3.20`, pulled on 24 Jul, fails.

An hourly canary in `toby`'s crontab starts those four images. By 15 Oct at 09:49 it hadn't failed once (`canary_fail.log` doesn't exist), so nothing new has broken since 14 Oct.

### 2.3 Listing images fails

`docker image ls` fails on this host, each time on a different missing file:

```text
Error response from daemon: rpc error: code = NotFound desc = blob sha256:69735e6a… expected at /data/containerd/io.containerd.content.v1.content/blobs/sha256/69735e6a…: blob not found: not found
```

Ten tries named ten different missing files. Their digests are spread across the whole range, from `09b515bf…` to `fffbbb45…`. So no user can easily see which of their images are broken.

### 2.4 What happened on 11 and 12 Oct

| Time (CST) | Event | Source |
|---|---|---|
| Before the restart | `/etc/fstab` has the `/data` line inside literal quotes: `"/dev/nvme1n1p1 /data ext4 defaults 0 0"` | `/etc/fstab.bak`. `findmnt --verify --tab-file /etc/fstab.bak` reports `parse error at line 12 -- ignored` |
| 11 Oct, 15:18 to 15:24 | Messages arrive late, some by 7 hours, and Tailscale loses its connection: the host is barely responsive | Journal, last lines of the boot |
| 11 Oct, 15:21 | The kernel runs out of memory and kills a `java` process. More kills follow until 12 Oct 10:36; all of them hit `python` or `java` processes | `/var/log/kern.log`, `/var/log/syslog` |
| 11 Oct, 15:23 and 15:31 | The system journal stops answering its watchdog and is killed twice. Its last entry is at 15:24; syslog keeps recording | `/var/log/syslog` |
| 12 Oct, 10:38 | Host boots, without a clean shutdown before it. The system disk runs `orphan cleanup` on mount. The `/data` disk is present at 10:38:10, and the boot finishes mounting local filesystems at 10:38:11 without `/data` | Kernel log, `systemctl show` |
| 10:38:15 to 10:40:15 | containerd starts, times out at 10:39:45, and starts again at 10:40:15. `/data` isn't mounted yet | Journal |
| 10:54:02 | `/etc/fstab.bak` is saved | File time |
| 10:54:05 | `/data` is mounted. ext4 reports `recovery complete` and `1386 orphan inodes deleted`. It has not been unmounted since | Kernel log, `systemctl show data.mount` |
| 11:33 and 11:41 | Two containers are created on the main Docker with layer folders 37 and 54. containerd is numbering from a new, empty database; the real one is past 350,000 | Mount table, `docker inspect` |
| 11:48 | Docker is stopped | `systemctl show docker.service` |
| 12:09:49 | `/etc/fstab` is rewritten as `UUID=25454905-… /data ext4 defaults 0 0`. Drop-ins `RequiresMountsFor=/data` and `AssertPathIsMountPoint=/data` are added to `containerd.service` and `docker.service` | File times |
| 13:07 | Docker starts again | `systemctl show docker.service` |
| 14:41 | The next container gets layer folder 354686, so containerd is back on the real database | Mount table, `docker inspect` |

The layer folder of each container mount on the main Docker, with the container's creation time (UTC, which is CST minus 8 hours):

```text
37       2026-10-12T03:33:21 f870b22117e6
54       2026-10-12T03:41:19 6afbbec81e30
354686   2026-10-12T06:41:54 fd19d30764d0
354842   2026-10-12T16:26:34 755ae3d88b91
```

containerd was restarted again on 14 Oct at 11:11 and at 17:36. The store was unchanged afterwards (7,089 images).

What the restart means:

- **"Without a clean shutdown"** means the host was powered off or reset while it was hung. A normal shutdown unmounts the disks, and ext4 then has nothing to recover.
- **The orphan inodes** are files that had already been deleted while a program still had them open. After a crash, ext4 finishes deleting them. That is normal and isn't, by itself, a sign of data loss.
- **Who restarted the host** isn't in the host's logs. The Alibaba Cloud console records instance restarts; root can check it (section 5.2).

### 2.5 Ruled out

- **Filesystem damage:** ext4's error counters for `/data` are 0. The recovery at 10:54 replayed ext4's journal, which is what ext4 does after any unclean stop.
- **A slow disk:** the `/data` disk was present at 10:38:10, and the boot never entered emergency mode.
- **Full disks:** the system disk is 66% used, and `/data` is 88% used.
- **Scheduled jobs:** no systemd timer, system cron job or tmpfiles rule touches Docker or `/data`. Per-user crontabs and user timers need root to read (Q8).
- **Containers with the host disk:** no container on the main Docker bind-mounts `/`, `/data` or containerd's folders.
- **Other daemons:**
  - The TaskAnchor runtime, set up on 10 Sep, keeps its own store in its own folder on `/data` and already requires the `/data` mount.
  - The daemons serving `/experiment-run/docker.sock` and `/isolated/docker` run inside containers of the main Docker.
- **Configuration changes:** containerd's and Docker's config files haven't changed since 24 Jul, when `/data` was set up.
- **Package upgrades:** Docker 29.6.2 and containerd 2.2.6 were installed on 24 Jul at 16:00 and haven't been upgraded since (`/var/log/apt/history.log*`). None of the October upgrades is Docker, containerd, runc, systemd or a library they load.
- **containerd being killed for memory:** none of the processes killed on 11 and 12 Oct was containerd or Docker.

## 3. Cause, as far as known

Every failure has the same shape: containerd's records point at data that is gone.

| What is missing | Error | Examples |
|---|---|---|
| A layer's folder on disk, while the layer store still lists it | `no such file or directory` | all 113 DeepSWE images |
| A downloaded file, while containerd's database still lists it | `blob not found` | `alpine:3.19`, `debian:bookworm-slim`, `ubuntu:22.04`, `ubuntu:24.04`, `docker image ls` |
| A layer's data, while containerd's main database still lists the layer | `AlreadyExists` or `missing parent` | uv, `alpine:3.23.4`, `alpine:3.20` |

### 3.1 The 12 Oct event (explains the missing layer folders)

1. The host had run out of memory and was restarted without a clean shutdown. This was the first boot that read the quoted `/data` line, so `/data` wasn't mounted.
2. containerd and Docker had no rule to wait for `/data` then, so they started on the empty `/data` folder of the system disk. containerd created a new, empty database there and kept it open.
3. `/data` was mounted at 10:54 while containerd was running. From then on, containerd's new files and folders went to the real disk; folders 37 and 54 are there. Its records stayed in the empty database, now hidden under the mount.
4. containerd's routine cleanup removes every layer folder that its database doesn't list. With an empty database, that is nearly every folder of the real store. The cleanup runs whenever a layer record is removed, for example when a container is removed. So anyone using Docker in that window could have triggered it without knowing.
5. When containerd restarted on the real database (between 11:41 and 14:41), the real records pointed at deleted folders.

This fits the evidence. The DeepSWE folders are gone while their downloaded files remain, folders 37 and 54 come from a second numbering, and everything pulled since 12 Oct works.

The step that turned "an empty store for 16 minutes" into data loss was step 3: mounting `/data` under a running containerd. Had containerd and Docker been stopped first, the real store would have been untouched.

### 3.2 Not explained yet

- **Missing downloaded files:** many old images have downloaded files missing (`blob not found`). containerd's cleanup of downloaded files goes through them in alphabetical order, and it would have removed DeepSWE's files too. Instead, the missing files are spread across the whole range while DeepSWE's survive. So they were probably deleted another way, or at another time.
- **Stale records from 6 and 7 Oct:** `alpine:3.23.4` (6 Oct) and `alpine:3.20` (7 Oct) failed before the restart, and there was no boot between 11 Sep and 12 Oct.
- **15 GB of syslog:** `/var/log/syslog.1`, covering 4 to 11 Oct, is 15 GB. The days since have produced far less.
- **Disk write stalls on 14 Oct:** from 20:21, the kernel reported `dockerd` blocked for more than 2 minutes, waiting for disk writes to finish. It doesn't delete data, but it shows that the `/data` disk can stall under load.

The candidates, and what each one predicts:

| Candidate | It predicts | Checked by |
|---|---|---|
| A clean-up that deleted files by age, for example `find … -mtime +30 -delete` run on `/data` to free space | Missing files are all older than some age; files still present are younger | Q7, Q8 |
| Files deleted by hand in `/data/containerd`, for example to free space (only root can write there) | A deletion in root's history, a root login or a sudo command at that time | Q2, Q8 |
| Files lost in the forced restart on 12 Oct | Only files written shortly before 12 Oct 10:38 are missing; the old images' files aren't | Q7, Q9 |
| An earlier memory crisis or hang, around 6 and 7 Oct | Out-of-memory kills or hung tasks in the kernel log on those days | Q6, Q9 |
| The same fstab problem at the 11 Sep boot | Low-numbered layer folders dated 11 Sep. Unlikely: images pulled on 10 Sep still worked on 8 Oct | Q5 |

### 3.3 What it means on a shared host

- The 12 Oct event wasn't caused by any user's work. It came from a configuration mistake, a host that ran out of memory, and a recovery step done while the services were running. Still, every user's images were affected.
- 12 accounts are in the docker group, which is root-equivalent: any of them could reach the store through a container that mounts the host. So the eventual prevention will need a team rule as well as technical guards.

## 4. Access for the investigation

### 4.1 Risk levels

Every command in the guide has one of these labels:

- **None:** it only reads. It writes nothing except files in Toby's own folder `~/snapshot-audit/`.
- **Low:** it makes a small change that removes no data and can be undone, for example a temporary read-only view of the system disk, or a test container that starts and is removed at once.
- **High:** no limits. The only high-risk command is opening the root shell in guide step 1.1, which is used only to install the rule.

### 4.2 The investigation-only sudo rule

Root gave Toby `root.pem` on 15 Oct. Toby uses it once, after root's OK, to install a sudo rule for `toby` (guide step 1). The rule is in `/etc/sudoers.d/90-toby-investigate`; the guide shows its exact text.

The rule lets `toby` run 17 exact commands as root, with fixed arguments. sudo refuses anything else, including the same program with other arguments. So `sudo -i`, `sudo bash`, `sudo rm`, `sudo systemctl`, `sudo ctr … rm` and any other `sudo mount` are refused. The rule parses with this host's `visudo` (`sudo-rs`) and with the classic `visudo.ws`; checked on 15 Oct.

| Allowed command | What it reads | Guide step |
|---|---|---|
| `ctr -n moby images ls -q` | Every image name | 3.3 |
| `ctr -n moby images check` | For each image: are all its downloaded files present, and is it unpacked | 5.1 |
| `ctr -n moby snapshots ls` | Every layer record | 5.1 |
| `ctr -n moby content ls` | Every downloaded-file record, with its age | 5.1 |
| `ctr -n moby content active` | Downloads in progress | 5.1 |
| `ls -l --full-time …/overlayfs/snapshots` | The layer folders on disk | 5.1 |
| `ls -U …/blobs/sha256` | The downloaded files on disk | 5.1 |
| `ls -ld --full-time …/snapshots/37 …/snapshots/54` | The two folders from the split brain | 6.5 |
| `ssh-keygen -lf /root/.ssh/authorized_keys` | The fingerprints of the keys that may log in as root | 6.1 |
| `cat /root/.bash_history` | Root's shell history | 6.2 |
| `grep -rH . /var/spool/cron/crontabs` | Every user's crontab | 6.3 |
| `ls -laR` of naiyu's, yun's and zihan's `~/.config/systemd/user` | File names of the three users' timers and services | 6.3 |
| `mkdir -p /mnt/rootfs`, `mount -o bind,ro / /mnt/rootfs`, `ls -la --full-time /mnt/rootfs/data …`, `du -sh /mnt/rootfs/data`, `umount /mnt/rootfs` (5 commands) | A temporary read-only view of the system disk, to see the store hidden under `/data` | 6.4 |

Why this is safe:

- **Only reading.** Every allowed command lists or reads, except the read-only view, which is mounted and removed in the same step. `ctr` has no read-only switch, so the rule fixes each command's arguments exactly.
- **Jenkins jobs can't use it.** The Jenkins agent also runs as `toby`. sudo asks for the password, and `sudo-rs` remembers it for 15 minutes only in the terminal where it was typed. A job has no terminal and no password.
- **Every sudo command is logged** in `/var/log/auth.log` and the journal (section 4.5).
- **It ends.** A timer removes the rule after 3 days, and root removes it by hand at the end (guide step 8.3).
- **Privacy:** the rule lets Toby read root's shell history and every crontab, but no other user's history or files. For three users who run background services (naiyu, yun and zihan), it lists only the file names of their user timers.

### 4.3 `root.pem`

`root.pem` is the private key that logs in as root on the worker. It gives unrestricted root access, so:

- It is kept on Toby's PC only, in `~/.ssh/cloud-worker-root.pem`, readable by Toby only (guide step 0.1). It is never copied to the worker, Jenkins or any git repository, and never pasted anywhere.
- It is used once, for guide step 1, after root's OK, and not again during the investigation.
- After the work, root decides whether Toby deletes it, and whether root removes the key from `/root/.ssh/authorized_keys`.

| Item | Value |
|---|---|
| Fingerprint (guide step 0.2) | |
| Root's OK to use it for step 1 (who, when) | |
| Used for step 1 (from, to) | |
| Rule installed, and `sudo -l -U toby` checked (time) | |

### 4.4 Rules Toby follows

1. Only the commands in the guide, copied exactly, one step at a time, from his own SSH terminal.
2. sudo only interactively. Never `sudo -i`, `sudo su` or `sudo bash`, and never sudo from cron, Jenkins, `nohup` or a background job. The rule would refuse the first three anyway.
3. If a command fails, or its result differs from the guide's "Expected", stop and ask. No improvising, and no "quick fix".
4. **Never run these during the investigation, even though some don't need sudo:**
   - `rm`, `mv` or `chmod` on anything under `/data`;
   - `docker rmi`, `docker image rm`, `docker system prune`, `docker image prune` or `docker builder prune`;
   - `docker pull`, `docker load`, `docker tag`, `docker stop` or `docker rm` on other users' images and containers;
   - `ctr … rm`, `ctr … remove`, `ctr … delete` or `ctr … prune`;
   - `systemctl stop`, `restart` or `kill` for `containerd`, `docker` or `data.mount`;
   - editing `/etc/fstab`, any file in `/etc/containerd`, `/etc/docker` or `/etc/systemd`, or mounting or unmounting `/data`.
5. Other users' data is read only as far as the guide says. Anything more needs root's and the owner's agreement.

### 4.5 How root can follow

- **Every sudo command** is in the journal: `journalctl _COMM=sudo --since today --no-pager` lists them, one line each, with the user and the command.
- **Every session** is recorded with `script` (guide step 2.3). The recordings are in `~toby/snapshot-audit/sessions/`, and root can read them. Passwords aren't shown on screen, so they aren't recorded.
- **What was done, and when,** is in the investigation log (section 6).

## 5. What the investigation must answer

### 5.1 The questions

| # | Question | Why it matters | Guide step | Evidence (in `~/snapshot-audit/`) |
|---|---|---|---|---|
| Q1 | At the 12 Oct boot, did containerd start before `/data` was mounted? | Confirms section 3.1, steps 1 and 2. Already seen on 15 Oct | 4.1 | `logs/boots.txt` |
| Q2 | Who wrote the quoted fstab line, and when? Who mounted `/data` at 10:54? | Shows which step failed: writing the line, checking it, or mounting under a running service | 4.3, 6.1, 6.2 | `logs/logins.txt`, `logs/root_logins_by_day.txt`, `logs/around_1012.txt`, `logs/root_keys.txt`, `logs/root_history.txt` |
| Q3 | Is the empty store from 12 Oct 10:38 still under the `/data` mount point? | Direct proof of the split brain (section 3.1, step 2) | 6.4 | `logs/hidden_store.txt` |
| Q4 | When did containerd switch back to the real database on 12 Oct, and who restarted it? | Bounds the window in which folders were deleted | 4.4 | `logs/day_1012.txt` |
| Q5 | Did the 11 Sep boot also run containerd without `/data`? | Would mean an earlier split brain | 4.1, 5.3 | `logs/boots.txt`, `inventory/low_numbered_dirs.txt` |
| Q6 | When did each kind of error first appear in the logs? | Dates the older damage, so the other logs can be read around that time | 3.2, 7.1 | `logs/damage_first_seen.txt` |
| Q7 | Do the missing downloaded files share an age? | Old files only means an age-based clean-up; files from 11 and 12 Oct only means the restart; no pattern rules out both | 5.2 | `inventory/blob_ages.txt` |
| Q8 | Did anyone delete files under `/data` by hand, or run a clean-up from a crontab or a user timer? | The other candidate for the missing files | 4.3, 4.6, 6.2, 6.3 | `logs/root_history.txt`, `logs/crontabs.txt`, `logs/user_timers.txt`, `logs/sudo_commands.txt` |
| Q9 | When did the host run out of memory or stall on disk writes, what was killed, and was there an earlier episode, for example on 6 and 7 Oct? | The 12 Oct restart came from it, and an earlier one could explain the 6 and 7 Oct damage | 4.2 | `logs/kernel.txt`, `logs/oom_per_day.txt`, `logs/oom_victims.txt`, `logs/hung_tasks.txt` |
| Q10 | What wrote 15 GB to syslog on 4 to 11 Oct? | It may be the same event, seen from another side | 3.2, 7.1 | `logs/syslog_volume.txt` |
| Q11 | Who or what restarted containerd on 14 Oct at 11:11 and 17:36? | A restart doesn't delete data, but an unexplained one means someone works on the store without telling the others | 4.5 | `logs/restarts_1014.txt` |
| Q12 | Do the two containers created during the split brain (folders 37 and 54) exist in the real database? | If not, they will fail at their next start, and their owners should know | 6.5 | `logs/split_brain_containers.txt` |

The aim is to find which step of the process failed, not to blame a person.

### 5.2 Ask first: the fastest answers

Several questions are answered fastest by asking. Toby asks root these today, in writing:

1. Was the host restarted on 12 Oct at about 10:37, from the Alibaba Cloud console or otherwise? By whom, and why? The console's instance history shows it. (Q9)
2. Who added the `/data` line to `/etc/fstab`, and when? (Q2)
3. Who mounted `/data` at 10:54 and rewrote fstab at 12:09? Was containerd stopped first? (Q2, Q4)
4. Has anyone deleted files in `/data/containerd` or `/data/docker`, for example to free space? When? (Q8)
5. Who restarted containerd on 14 Oct at 11:11 and 17:36? (Q11)

And the users of the docker group, in the team chat:

1. Did any job use a lot of memory on 11 Oct from 15:00? (Q9)
2. Does anyone run a clean-up script or service that deletes files under `/data`? (Q8)

The logs then confirm or correct the answers.

| Question | Answer | From, when |
|---|---|---|
| Root 1: restart on 12 Oct | | |
| Root 2: the fstab line | | |
| Root 3: mount at 10:54, fstab at 12:09 | | |
| Root 4: deletes in `/data` | | |
| Root 5: containerd restarts on 14 Oct | | |
| Users 1: memory on 11 Oct | | |
| Users 2: clean-up scripts | | |

## 6. Investigation log

One row per guide step: when it was done, the result in one line, and where the evidence is. "Sent" means the step's output was shared for review.

| Guide step | What | Done (date, time) | Result | Evidence | Sent |
|---|---|---|---|---|---|
| (before) | Read-only checks of 14 and 15 Oct (Appendix A) | 14 Oct; 15 Oct 10:48 to 11:00 | Sections 2 and 3 | `facts.txt`, `base_status.tsv`, `deepswe_status.tsv`, `cause_checks.txt` | yes |
| 0.1 | `root.pem` moved to `~/.ssh`, mode 600 | | | | |
| 0.2 | Key fingerprint | | | section 4.3 | |
| 0.3 | Test login as root | | | | |
| 1 | Sudo rule installed (1.1 to 1.8) | | | `sudo -l -U toby` output | |
| 2.2 | Groups and rule checked as `toby` | | | | |
| 2.3 | Recording started | | | `sessions/<date_time>.log` | |
| 2.4 | Scripts saved | | | `long_scans.sh`, `layer_test.sh` | |
| 3.1 | Logs copied | | | `logcopy/` | |
| 3.2 | Slow log scans started | | | `logs/long_scans.log` | |
| 3.3 | Image list; start test started | | | `inventory/images_list.txt`, `inventory/layer_test.log` | |
| 4.1 | Boots (Q1, Q5) | | | `logs/boots.txt` | |
| 4.2 | Memory, stuck processes, disk (Q9) | | | `logs/kernel.txt`, `logs/oom_per_day.txt`, `logs/oom_victims.txt`, `logs/hung_tasks.txt`, `logs/journald_watchdog.txt` | |
| 4.3 | Logins and sudo (Q2, Q8, Q11) | | | `logs/logins.txt`, `logs/sudo_commands.txt`, `logs/root_logins_by_day.txt`, `logs/around_1012.txt` | |
| 4.4 | 12 Oct in detail (Q4) | | | `logs/day_1012.txt` | |
| 4.5 | 14 Oct restarts (Q11) | | | `logs/restarts_1014.txt` | |
| 4.6 | Toby's own timers and crontab (Q8) | | | session recording | |
| 5.1 | containerd's lists | | | `inventory/images_check.txt`, `inventory/snapshots_db.txt`, `inventory/content_records.txt`, `inventory/content_active.txt`, `inventory/snapshot_dirs.txt`, `inventory/blob_files.txt` | |
| 5.2 | Records against disk; ages (Q7) | | | `inventory/blobs_missing.txt`, `inventory/blobs_unrecorded.txt`, `inventory/blob_ages.txt` | |
| 5.3 | Low-numbered folders (Q5) | | | `inventory/low_numbered_dirs.txt` | |
| 6.1 | Root's keys (Q2) | | | `logs/root_keys.txt` | |
| 6.2 | Root's history (Q2, Q8) | | | `logs/root_history.txt` | |
| 6.3 | Crontabs and user timers (Q8) | | | `logs/crontabs.txt`, `logs/user_timers.txt` | |
| 6.4 | The hidden store (Q3) | | | `logs/hidden_store.txt` | |
| 6.5 | The split-brain containers (Q12) | | | `logs/split_brain_containers.txt` | |
| 7.1 | Slow log scans (Q6, Q10) | | | `logs/damage_errors.txt`, `logs/damage_first_seen.txt`, `logs/syslog_volume.txt` | |
| 7.2 | Start test (impact) | | | `inventory/layer_test.tsv`, `inventory/broken_by_repo.txt`, `inventory/containers_on_broken.tsv` | |
| 8.1 | Recording stopped | | | `sessions/` | |
| 8.2 | Evidence copied to Toby's PC | | | `~/cloud-worker-evidence/` on Toby's PC | |
| 8.3 | Access removed by root | | | | |

Anything that didn't go as the guide expected:

| Time | Step | What happened | What was decided |
|---|---|---|---|
| | | | |

## 7. Investigation result

Fill this in as the steps finish. Status is confirmed, ruled out or open.

### 7.1 Answers

| # | Answer | Evidence | Status |
|---|---|---|---|
| Q1 | containerd started at 10:38:15 and 10:40:15; `/data` was mounted at 10:54:05 (seen on 15 Oct; guide step 4.1 records it) | `logs/boots.txt` | confirmed |
| Q2 | | `logs/around_1012.txt`, `logs/root_keys.txt`, `logs/root_history.txt`, root's answer | |
| Q3 | | `logs/hidden_store.txt` | |
| Q4 | | `logs/day_1012.txt` | |
| Q5 | | `logs/boots.txt`, `inventory/low_numbered_dirs.txt` | |
| Q6 | | `logs/damage_first_seen.txt` | |
| Q7 | | `inventory/blob_ages.txt` | |
| Q8 | | `logs/root_history.txt`, `logs/crontabs.txt`, `logs/user_timers.txt` | |
| Q9 | Out of memory from 11 Oct 15:21 to 12 Oct 10:36; journal killed; restart without a clean shutdown at 10:38. `dockerd` stalled on disk writes on 14 Oct from 20:21 (both seen on 15 Oct). Still open: who restarted the host, and whether there were earlier episodes | `logs/kernel.txt`, `logs/oom_per_day.txt`, `logs/hung_tasks.txt`, root's answer | partly confirmed |
| Q10 | | `logs/syslog_volume.txt` | |
| Q11 | | `logs/restarts_1014.txt`, root's answer | |
| Q12 | | `logs/split_brain_containers.txt` | |

| Inventory | Count | File |
|---|---|---|
| Images in the store | (7,089 on 14 Oct) | `inventory/images_list.txt` |
| `images check`: complete / incomplete | | `inventory/images_check.txt` |
| Start test: `ok` / `layers_missing` / `blob_missing` / `stale_record` / `other` | | `inventory/layer_test.tsv` |
| Layer records / layer folders | | `inventory/snapshots_db.txt`, `inventory/snapshot_dirs.txt` |
| Downloaded-file records / recorded but missing / on disk but not recorded | | `inventory/blobs_*.txt` |
| Repositories with broken images / containers on broken images | | `inventory/broken_by_repo.txt`, `inventory/containers_on_broken.tsv` |

### 7.2 How to decide the cause of the missing downloaded files and the stale records

| What the evidence shows | Conclusion |
|---|---|
| `blob_ages.txt`: every `missing` line is older than every `present` line, for example missing only at "2 months" and older | Deleted by age. The deleting job or person is in Q8's evidence or root's answer |
| `blob_ages.txt`: the missing files are mostly 3 to 4 days old (11 and 12 Oct) | Lost in the forced restart |
| `blob_ages.txt`: missing and present files have the same spread of ages | Neither age nor the restart. Look at `root_history.txt` and root's answer for deletes by hand |
| `oom_per_day.txt` or `hung_tasks.txt` shows out-of-memory kills or hung tasks on 6 or 7 Oct | The stale records of 6 and 7 Oct probably come from an earlier memory crisis or disk stall |
| `damage_first_seen.txt` shows an error type well before 12 Oct | That damage predates the restart; read `logins.txt`, `root_history.txt` and `kernel.txt` around that date |

### 7.3 Conclusions

- Cause of the missing layer folders:
- Cause of the missing downloaded files:
- Cause of the stale records from 6 and 7 Oct:
- What started the 12 Oct chain (memory, restart):
- Who and which jobs are affected:
- Still open:

The repair and prevention plan will be written after the mentor and root have reviewed this result. Until then, nothing in the store is changed, and mac-k3d jobs run on the local worker (`AGENT_LABEL=linux-Michael-Ubuntu`).

## Appendix A: checks already run as `toby` (no sudo)

All read-only. The 12 Oct chain (sections 2.1 to 2.4):

```bash
# boot, /data mount, fstab history and the guards
uptime -s
systemctl show data.mount -p ActiveEnterTimestamp -p ActiveExitTimestamp
systemctl show emergency.target local-fs.target -p Id -p ActiveEnterTimestamp
systemctl show docker.service -p ActiveExitTimestamp -p InactiveExitTimestamp
ls -l --time-style=full-iso /etc/fstab /etc/fstab.bak /etc/systemd/system/containerd.service.d/ /etc/systemd/system/docker.service.d/
diff /etc/fstab.bak /etc/fstab
findmnt --verify --tab-file /etc/fstab.bak
cat /sys/fs/ext4/nvme1n1p1/errors_count
# layer folder of each container mount on the main Docker, with the container's creation time (UTC)
grep ' - overlay ' /proc/self/mountinfo | grep ' /data/docker/rootfs/overlayfs/' | while read -r line; do
  sid=$(grep -oE 'upperdir=[^,]+' <<<"$line" | grep -oE 'snapshots/[0-9]+' | cut -d/ -f2)
  cid=$(awk '{print $5}' <<<"$line"); cid=${cid##*/}
  printf '%-8s %s %s\n' "$sid" "$(docker inspect --format '{{.Created}}' "$cid" 2>/dev/null | cut -c1-19)" "${cid:0:12}"
done | sort -n
# one DeepSWE image: are its downloaded files still there? (reads about 840 MB, writes nothing)
docker save public.ecr.aws/d3j8x8q7/swe-bench-202605:kh75679ajj3b8dtd7se3h7z0a1833y6r-v1.1 | wc -c
# missing downloaded files named by docker image ls, ten tries
for n in $(seq 10); do docker image ls -q 2>&1 >/dev/null | grep -oE 'blob sha256:[0-9a-f]{64}' | head -n 1; done | sort -u
# Docker and containerd package history
zgrep -hE 'Start-Date|Commandline|containerd|docker' /var/log/apt/history.log*
```

The checks behind section 2.5. They write `~/snapshot-audit/cause_checks.txt`:

```bash
cd ~/snapshot-audit
{
  echo "== boot and config changes"
  uptime -s
  stat -c '%y  %n' /etc/containerd/config.toml /etc/docker/daemon.json
  ls -ld --time-style=long-iso /var/lib/containerd /data/containerd /etc/taskanchor-runtime 2>&1
  echo "== which service or container runs each daemon"
  for p in $(pgrep -x dockerd) $(pgrep -x containerd); do
    printf '%s\t%s\t%s\n' "$p" "$(sed -n 's/^0:://p' "/proc/$p/cgroup")" "$(tr '\0' ' ' <"/proc/$p/cmdline" | cut -c1-90)"
  done
  echo "== storage settings of the other runtime"
  grep -E '"(data-root|containerd)"' /etc/taskanchor-runtime/daemon.json 2>&1
  grep -nE '^[[:space:]]*(root|state)[[:space:]]*=' /etc/taskanchor-runtime/containerd.toml 2>&1
  echo "== scheduled jobs and tmpfiles rules that mention Docker, /data or deleting"
  systemctl list-timers --all --no-pager
  grep -rlsE 'containerd|/data|prune|rm -rf|-delete' /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly
  grep -rns '/data' /etc/tmpfiles.d /usr/lib/tmpfiles.d /run/tmpfiles.d || echo "no tmpfiles rule mentions /data"
  echo "== containers that bind-mount the host's /, /data or containerd's folders"
  docker ps -aq | xargs -r docker inspect --format '{{.Name}} {{.State.Status}}{{range .Mounts}}{{if eq .Type "bind"}} {{.Source}}{{end}}{{end}}' 2>/dev/null \
    | grep -E ' (/|/data|/data/containerd[^ ]*|/var/lib/containerd[^ ]*)( |$)'
} > cause_checks.txt 2>&1
cat cause_checks.txt
```

The checks of 15 Oct behind section 2.4: the 11 and 12 Oct gap, and log coverage:

```bash
journalctl --list-boots --no-pager
journalctl -b -1 -n 12 -o short-iso --no-pager
journalctl -b 0 -o short-iso --no-pager -u data.mount -u containerd.service -u docker.service | grep -m 8 -E 'Mount|Start|Fail'
journalctl -b 0 -k -o short-iso --no-pager | grep -iE 'ext4|recovery|orphan|nvme1n1'
r='^2026-10-1(1T(15:[2-5]|1[6-9]|2)|2T(0|10:[0-3]))'
grep -E "$r" /var/log/syslog | grep -iE 'out of memory|oom-kill|killed process|watchdog|shutdown|power|reboot'
grep -E "$r" /var/log/syslog | grep -oE 'Killed process [0-9]+ \([^)]+\)' | awk '{print $4}' | sort | uniq -c
grep -E '^2026-10-14T20:(1[5-9]|2[0-2])' /var/log/kern.log | grep -E 'blocked for more than|waiting for writeback'
journalctl --no-pager -o short-iso -q -u containerd | head -n 1
for f in /var/log/syslog.4.gz /var/log/auth.log.4.gz /var/log/kern.log.4.gz; do zcat "$f" | head -n 1 | cut -c1-26; done
```

## Appendix B: where the evidence is

On the worker, in `/home/toby/snapshot-audit/`. A copy goes to `~/cloud-worker-evidence/` on Toby's PC (guide step 8.2), outside any git repository.

**From 14 Oct (before the guide):**

| File | Contents |
|---|---|
| `facts.txt` | Host facts (section 1) |
| `base_status.tsv` | The eleven common images: `ok`, `absent`, `pull_failed` or `run_failed`, with the error |
| `deepswe_images.tsv`, `deepswe_status.tsv` | The 113 DeepSWE images (question, then image) and their results |
| `damaged_chains.txt` | The layer IDs named in the errors: `sha256:51e78f04…`, `sha256:29df493b…`, `sha256:08bc4e…` |
| `cause_checks.txt` | The checks behind section 2.5. Its daemon column came from an earlier version of the command and is wrong; Appendix A has the corrected version |
| `damaged_chain_images.tsv`, `deepswe_pull_sample.tsv`, `image_ids.txt` | Empty: from checks that needed root. Guide step 5 replaces them |
| `canary_fail.log` | Not created yet. An hourly line in `toby`'s crontab starts the four working images, and writes here only if one fails |

**From the guide:**

| Folder | Files | Guide step | Answers |
|---|---|---|---|
| `sessions/` | One recording per work session | 2.3 | How every step was run |
| (top) | `long_scans.sh`, `layer_test.sh` | 2.4 | |
| `logcopy/` | Copies of `syslog`, `auth.log` and `kern.log` from 13 Sep, and the containerd and Docker journal | 3.1 | Kept after the weekly rotation |
| `logs/` | `boots.txt` | 4.1 | Q1, Q5 |
| `logs/` | `kernel.txt`, `oom_per_day.txt`, `oom_victims.txt`, `hung_tasks.txt`, `journald_watchdog.txt` | 4.2 | Q9 |
| `logs/` | `logins.txt`, `sudo_commands.txt`, `root_logins_by_day.txt`, `around_1012.txt` | 4.3 | Q2, Q8, Q11 |
| `logs/` | `day_1012.txt` | 4.4 | Q4 |
| `logs/` | `restarts_1014.txt` | 4.5 | Q11 |
| `logs/` | `root_keys.txt` | 6.1 | Q2 |
| `logs/` | `root_history.txt` | 6.2 | Q2, Q8 |
| `logs/` | `crontabs.txt`, `user_timers.txt` | 6.3 | Q8 |
| `logs/` | `hidden_store.txt` | 6.4 | Q3 |
| `logs/` | `split_brain_containers.txt` | 6.5 | Q12 |
| `logs/` | `long_scans.log`, `damage_errors.txt`, `damage_first_seen.txt`, `syslog_volume.txt` | 3.2, 7.1 | Q6, Q10 |
| `inventory/` | `images_list.txt`, `layer_test.log`, `layer_test.tsv` | 3.3, 7.2 | Impact |
| `inventory/` | `images_check.txt`, `snapshots_db.txt`, `content_records.txt`, `content_active.txt`, `snapshot_dirs.txt`, `blob_files.txt` | 5.1 | Q7, Q12, impact |
| `inventory/` | `blobs_db.txt`, `blob_files.sorted`, `blobs_missing.txt`, `blobs_unrecorded.txt`, `blob_ages.txt` | 5.2 | Q7 |
| `inventory/` | `low_numbered_dirs.txt` | 5.3 | Q5 |
| `inventory/` | `broken_by_repo.txt`, `containers_on_broken.tsv` | 7.2 | Impact |
