# Cloud worker investigation: step by step

- **For:** Toby, on the cloud worker `iZt4ndd2dff7gqjta7mppaZ` (`47.84.16.18`).
- **Goal for today:** collect the evidence that answers Q1 to Q12 in `CLOUD_WORKER_DOCKER_REPORT.md`, without changing anything in the Docker store.
- **Record:** after each step, fill in its row in the report's investigation log (`CLOUD_WORKER_DOCKER_REPORT.md`, section 6), and paste the step's "Send me" output into the chat. The next steps are adjusted from what comes back.

Nothing in this guide repairs, removes or restarts anything. The only changes to the server are:
- step 1: a password for `toby` and one sudo rule;
- step 6.4: an empty folder and a temporary read-only view.

All of them are small and can be undone.

## Before you start

### How each step is written

- **Where:** the terminal to use: your PC, root on the worker, or `toby` on the worker.
- **Why:** what the step tells us, and how it fits the investigation.
- **Risk:**
  - **none:** it only reads, and writes only to your own folder `~/snapshot-audit/`;
  - **low:** a small change that removes no data and can be undone;
  - **high:** no limits. Only step 1.1 is high, and only because it opens a root shell.
- **Command:** copy the whole grey block and paste it into the terminal. Lines that start with `#`, and text after `#` at the end of a line, are comments; the shell ignores them.
- **Expected:** what you should see.
- **Send me:** what to paste back into the chat.

### Ground rules

1. Copy the commands exactly. Don't edit them, and don't add `sudo` where there is none.
2. Wait for the prompt to come back before pasting the next block.
3. If the output differs from **Expected**, stop and send me the output. Don't try a fix.
4. Never paste a password, or the contents of `root.pem`, into the chat or anywhere else. Fingerprints (`SHA256:…`) aren't secret and are fine to share.
5. sudo asks for `toby`'s password. Nothing appears while you type it; that is normal.

### Know where you are

The prompt shows which machine you are on, and as which user:

| Prompt ends with | Where you are | Care |
|---|---|---|
| `Toby@<your PC>:~$` | Your own PC | Normal |
| `toby@iZt4ndd2dff7gqjta7mppaZ:~$` | The worker, as `toby` | Normal. sudo allows only the 17 commands of step 1 |
| `root@iZt4ndd2dff7gqjta7mppaZ:~#` | The worker, as root | No limits. Type only the commands of step 1, then `exit` |

Use two terminals on your PC:

- **Terminal 1:** the worker as `toby`, from step 2 onwards, for most of the work.
- **Terminal 2:** your PC, for steps 0 and 1, and to check on long jobs.

### What you are looking at: the store in five points

1. **Where it lives.** Docker on this host keeps its images through containerd, in `/data/containerd`. An image is a stack of layers.
2. **Each layer exists twice:**
   - **Downloaded file (a "blob"):** the compressed layer as pulled from the registry, in `/data/containerd/io.containerd.content.v1.content/blobs/sha256/<digest>`.
   - **Unpacked folder (a "snapshot"):** the same layer unpacked, so that a container can use it, in `/data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/<number>/fs`.
3. **containerd keeps records of both, in two databases:**
   - `io.containerd.metadata.v1.bolt/meta.db` for images and downloaded files;
   - `io.containerd.snapshotter.v1.overlayfs/metadata.db` for unpacked folders.
4. **The damage on this host always has the same shape: a record whose data is gone.** There are three kinds:
   - a downloaded file is missing (`blob not found`);
   - an unpacked folder is missing (`no such file or directory`);
   - a record has no usable data behind it (`already exists`, `missing parent`).
5. **containerd's cleanup deletes data that no record points to.** That is safe while the records are right. On 12 Oct, containerd ran for a while with an empty database, and its cleanup deleted the real folders.

The investigation asks when, and by what, data disappeared while its records stayed. The evidence comes from three places:

- the logs: who logged in, what restarted, when the host ran out of memory;
- containerd's records, compared with what is on disk;
- the ages of the missing files.

## Step 0: make `root.pem` safe, and test it (your PC, 5 minutes)

### 0.1 Move the key to `~/.ssh` and make it private

- **Where:** terminal 2, your PC.
- **Why:** `root.pem` is in `~/Downloads`, and every account on your PC can read it (`-rw-rw-r--`). Whoever has it can log in as root on the worker. `ssh` also refuses a key that others can read.
- **Risk:** none. It moves one file on your PC and changes its permissions.

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh                       # your private SSH folder
mv ~/Downloads/root.pem ~/.ssh/cloud-worker-root.pem      # out of Downloads, with a clear name
chmod 600 ~/.ssh/cloud-worker-root.pem                    # readable by you only
ls -l ~/.ssh/cloud-worker-root.pem
```

- **Expected:** `-rw------- 1 Toby Toby … /home/Toby/.ssh/cloud-worker-root.pem`.
- **Send me:** the `ls` line.

### 0.2 Show the key's fingerprint

- **Where:** terminal 2, your PC.
- **Why:** the fingerprint identifies the key without revealing it. Root can confirm it's the key he gave you, and step 6.1 later shows which root logins used it.
- **Risk:** none. It reads the key and prints only its fingerprint.

```bash
ssh-keygen -lf ~/.ssh/cloud-worker-root.pem
```

- **Expected:** one line like `2048 SHA256:… (RSA)`. If it asks for a passphrase, root has to give it to you.
- **Send me:** that line.

### 0.3 Test the key without opening a shell

- **Where:** terminal 2, your PC.
- **Why:** check that the key logs in as root on this worker, before you rely on it in step 1.
- **Risk:** none. It logs in as root, runs three commands that only print, and logs out. `IdentitiesOnly=yes` makes ssh offer this key only.

```bash
ssh -i ~/.ssh/cloud-worker-root.pem -o IdentitiesOnly=yes root@47.84.16.18 'whoami; hostname; date'
```

- **Expected:** `root`, `iZt4ndd2dff7gqjta7mppaZ` and the current date.
  - If you see `Permission denied (publickey)`, the key isn't accepted for root on this host. Stop, and give root the fingerprint from 0.2.
  - If you see `UNPROTECTED PRIVATE KEY FILE`, run 0.1 again.
- **Send me:** the output.

## Step 1: install the investigation-only sudo rule (root on the worker, 10 minutes)

The rule lets `toby` run 17 exact commands as root, each with fixed arguments, and nothing else. It needs `toby`'s password, so Jenkins jobs (which also run as `toby`) can't use it. After this step you don't need `root.pem` again for the investigation.

**First, message root** and wait for an OK:

```text
I'll use root.pem once now to install the investigation-only sudo rule from
CLOUD_WORKER_DOCKER_REPORT.md section 4.2 (17 read-only commands, password required,
removed automatically after 3 days). I'll set a password for toby, run nothing else
as root, and log out. You can check it with: sudo -l -U toby
```

### 1.1 Open a root shell

- **Where:** terminal 2.
- **Why:** installing a sudo rule needs root.
- **Risk:** **high.** A root shell has no limits. Type only steps 1.2 to 1.7, then step 1.8 (`exit`).

```bash
ssh -i ~/.ssh/cloud-worker-root.pem -o IdentitiesOnly=yes root@47.84.16.18
```

- **Expected:** the prompt `root@iZt4ndd2dff7gqjta7mppaZ:~#`.

### 1.2 Give `toby` a password

- **Why:** sudo asks for it; `toby`'s password is locked now.
- **Risk:** low. It changes only `toby`'s password. Password logins over SSH are off for every account, so it opens no new way in. It can be undone with `passwd -l toby`.

```bash
passwd toby
```

- **Expected:** it asks for the new password twice, without showing it, then prints `passwd: password updated successfully`. Choose a password you can type often; sudo asks for it every 15 minutes.

### 1.3 Write the rule to a temporary file

- **Why:** prepare the rule, so that it can be checked before it is installed. Each line is one command `toby` may run. Section 4.2 of the report explains each one.
- **Risk:** none. It writes one file in `/tmp`, which nothing reads yet.

```bash
cat > /tmp/90-toby-investigate <<'EOF'
# Investigation of the Docker store: exact, read-only commands only
Cmnd_Alias MK3D_INVESTIGATE = \
    /usr/bin/ctr -n moby images check, \
    /usr/bin/ctr -n moby images ls -q, \
    /usr/bin/ctr -n moby snapshots ls, \
    /usr/bin/ctr -n moby content ls, \
    /usr/bin/ctr -n moby content active, \
    /usr/bin/ls -l --full-time /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots, \
    /usr/bin/ls -U /data/containerd/io.containerd.content.v1.content/blobs/sha256, \
    /usr/bin/ls -ld --full-time /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/37 /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/54, \
    /usr/bin/ssh-keygen -lf /root/.ssh/authorized_keys, \
    /usr/bin/cat /root/.bash_history, \
    /usr/bin/grep -rH . /var/spool/cron/crontabs, \
    /usr/bin/ls -laR /home/naiyu/.config/systemd/user /home/yun/.config/systemd/user /home/zihan/.config/systemd/user, \
    /usr/bin/mkdir -p /mnt/rootfs, \
    /usr/bin/mount -o bind\,ro / /mnt/rootfs, \
    /usr/bin/ls -la --full-time /mnt/rootfs/data /mnt/rootfs/data/containerd /mnt/rootfs/data/containerd/io.containerd.metadata.v1.bolt, \
    /usr/bin/du -sh /mnt/rootfs/data, \
    /usr/bin/umount /mnt/rootfs
toby ALL=(root) MK3D_INVESTIGATE
EOF
```

- **Expected:** no output; the prompt comes back.

### 1.4 Check the rule, and install it only if the check passes

- **Why:** a broken sudoers file could stop sudo working for everyone. `visudo -c` checks the file, and `&&` installs it only if the check passed.
- **Risk:** low. It adds one file to `/etc/sudoers.d/`, which `rm -f /etc/sudoers.d/90-toby-investigate` removes again.

```bash
visudo -c -f /tmp/90-toby-investigate && install -m 0440 -o root -g root /tmp/90-toby-investigate /etc/sudoers.d/90-toby-investigate
```

- **Expected:** `/tmp/90-toby-investigate: parsed OK`. If it prints anything else, stop: nothing was installed. Send me the output.

### 1.5 Remove the temporary file

- **Risk:** none. It removes only the file from 1.3.

```bash
rm -f /tmp/90-toby-investigate
```

- **Expected:** no output.

### 1.6 Show what `toby` may now run

- **Why:** confirm that the rule is active, and that it's the only one.
- **Risk:** none. It only reads.

```bash
sudo -l -U toby
```

- **Expected:** the 17 commands of 1.3, run as `(root)`, and nothing else.
- **Send me:** the output.

### 1.7 Remove the rule automatically after 3 days

- **Why:** a backstop, in case removing it by hand (step 8.3) is forgotten.
- **Risk:** low. In 3 days it deletes only the file from 1.4.

```bash
systemd-run --on-active=3d --unit=toby-investigate-expire /usr/bin/rm -f /etc/sudoers.d/90-toby-investigate
```

- **Expected:** `Running timer as unit: toby-investigate-expire.timer`.

### 1.8 Leave the root shell

- **Risk:** none.

```bash
exit
```

- **Expected:** the prompt of your PC again. Don't use `root.pem` again for the investigation. Keep it in `~/.ssh`, and don't delete it until root says so.

**Then message root:** "Done at <time>: rule `/etc/sudoers.d/90-toby-investigate` installed, timer `toby-investigate-expire` set for 3 days. Nothing else changed."

## Step 2: start work as `toby` (5 minutes)

### 2.1 Log in as `toby`

- **Where:** terminal 1.
- **Risk:** none.

```bash
ssh toby@47.84.16.18
```

- **Expected:** the prompt `toby@iZt4ndd2dff7gqjta7mppaZ:~$`.

### 2.2 Check your groups and your sudo rule

- **Why:**
  - `adm` lets you read `/var/log`; `systemd-journal` lets you read the journal; `docker` lets you run containers.
  - `sudo -l` shows the rule from step 1.
  - `sudo true` shows that anything outside the rule is refused.
- **Risk:** none. `sudo true` is refused, and `true` does nothing anyway.

```bash
id
sudo -l
sudo true
```

- **Expected:**
  - `id` lists `adm`, `docker` and `systemd-journal`.
  - `sudo -l` asks for the password from 1.2 and lists the 17 commands.
  - `sudo true` is refused with a message that `toby` may not run it.
- **Send me:** the output, without the password; it isn't shown anyway.

### 2.3 Create your folders, and start recording the session

- **Why:** all evidence goes into `~/snapshot-audit/`. `script` records everything that appears on screen into `sessions/`, so root and the mentor can check what was run. Passwords aren't shown, so they aren't recorded.
- **Risk:** none.

```bash
mkdir -p ~/snapshot-audit/logs ~/snapshot-audit/inventory ~/snapshot-audit/logcopy ~/snapshot-audit/sessions
cd ~/snapshot-audit/logs
script -q -a ~/snapshot-audit/sessions/"$(date +%F_%H%M)".log
```

- **Expected:** no output; a new prompt. Everything is recorded until step 8.1.
- **If the connection drops later:** log in again, run this block again, and carry on. The background jobs of step 3 keep running.

### 2.4 Save the two scripts

- **Why:**
  - `long_scans.sh` does the two slow log scans: Q6, when each error first appeared, and Q10, what filled syslog on 4 to 11 Oct.
  - `layer_test.sh` starts every image once, to sort the broken ones by type of damage.
  
  Neither uses sudo, and neither removes anything.
- **Risk:** none. It writes two files in your folder, and `bash -n` checks them without running them.

```bash
cat > ~/snapshot-audit/long_scans.sh <<'EOF'
#!/bin/bash
# The two slow log scans: Q6 (first appearance of each error) and Q10 (what filled syslog on 4-11 Oct).
# Reads the logs and writes only into ~/snapshot-audit/logs. Run before the weekly rotation on Sunday 18 Oct.
cd ~/snapshot-audit/logs || exit 1
echo "start $(date -Is)"
zgrep -hiE '(containerd|dockerd)\[[0-9]+\]: .*(blob not found|failed to stat parent|snapshot.*already exists|missing parent|no space left)' \
  /var/log/syslog.4.gz /var/log/syslog.3.gz /var/log/syslog.2.gz /var/log/syslog.1 /var/log/syslog > damage_errors.txt
for k in 'blob not found' 'failed to stat parent' 'already exists' 'missing parent' 'no space left'; do
  l=$(grep -m 1 -i "$k" damage_errors.txt)
  printf '== %s: %s\n' "$k" "${l:-never}" | cut -c1-250
done > damage_first_seen.txt
echo "Q6 done $(date -Is)"
awk '{d = substr($1, 1, 10); b[d] += length($0) + 1; p = $3; sub(/\[.*/, "", p); sub(/:$/, "", p); n[p]++}
     END {for (d in b) printf "day %s %d MB\n", d, b[d] / 1048576; for (p in n) printf "prog %d %s\n", n[p], p}' \
  /var/log/syslog.1 > syslog_volume.txt
echo "Q10 done $(date -Is)"
EOF
cat > ~/snapshot-audit/layer_test.sh <<'EOF'
#!/bin/bash
# Start each image once, with no network and no pull, and record whether its layers mount.
# Usage: layer_test.sh <image list> <output .tsv>. Needs no sudo, and never removes an image.
in=${1:?image list}
out=${2:?output file}
grep -vE '^(sha256:|moby-dangling@)' "$in" | while read -r img; do
  msg=$(timeout 120 docker run --rm --pull never --network none --entrypoint true "$img" 2>&1 < /dev/null)
  case $msg in
    "") s=ok ;;
    *"failed to stat parent"*|*"lowerdir="*|*"overlayfs/snapshots/"*) s=layers_missing ;;
    *"blob not found"*) s=blob_missing ;;
    *"already exists"*|*"missing parent"*) s=stale_record ;;
    *"executable file not found"*) s=ok ;;   # the layers mounted; the image just has no "true"
    *) s=other ;;
  esac
  printf '%s\t%s\t%s\n' "$s" "$img" "$(printf '%s' "$msg" | tr '\n\t' '  ' | cut -c1-200)"
done > "$out"
EOF
bash -n ~/snapshot-audit/long_scans.sh && bash -n ~/snapshot-audit/layer_test.sh && echo "scripts OK"
```

- **Expected:** `scripts OK`.

## Step 3: start the long jobs first (15 minutes)

The slow jobs start now, so they run while you do steps 4 to 6.

### 3.1 Copy the logs before the weekly rotation deletes the oldest week

- **Why:** the logs are the evidence. On Sunday 18 Oct, the weekly rotation deletes 13 to 20 Sep from `/var/log`. The journal is capped at 3.9 GB, so containerd's entries from 10 Oct will go too, eventually.
- **Risk:** none. It reads the logs, and writes about 4 GB into your home on the system disk, which has 165 GB free. `df` shows the space first.

```bash
df -h /home                                      # free space; needs more than 10 GB
cp -p /var/log/syslog.*.gz /var/log/auth.log.*.gz /var/log/kern.log.*.gz /var/log/auth.log.1 /var/log/kern.log.1 ~/snapshot-audit/logcopy/
nice journalctl -u containerd -u docker -u data.mount -o short-iso --no-pager | gzip > ~/snapshot-audit/logcopy/journal_containerd_docker.txt.gz
ls -lh ~/snapshot-audit/logcopy/
```

- **Expected:**
  - `df` shows about 165 GB available.
  - After 1 to 3 minutes, `ls` lists 12 files: nine `.gz` logs (about 3 GB together), `auth.log.1` (150 MB), `kern.log.1` (27 MB), and the journal export.
- **Send me:** the `df` line and the `ls` output.

### 3.2 Start the two slow log scans in the background

- **Why:** they read about 20 GB of syslog, which takes 10 to 20 minutes. `nohup … &` runs them in the background, and they keep running if the connection drops. `nice` gives other users' work priority.
- **Risk:** none. No sudo; they read logs and write three files in `~/snapshot-audit/logs/`.

```bash
cd ~/snapshot-audit/logs
nohup nice bash ~/snapshot-audit/long_scans.sh > long_scans.log 2>&1 &
```

- **Expected:** a job number and a process ID, for example `[1] 123456`. Check progress at any time with `cat ~/snapshot-audit/logs/long_scans.log`. You get the results in step 7.1.

### 3.3 List the images, and start the start test

- **Why:**
  - **The image list.** `ctr images ls -q` asks containerd for every image name. `docker image ls` can't be used, because it fails on this host (report, section 2.3).
  - **The start test.** It starts each image once, with no network and no pull, and sorts the failures by type of damage. That gives the full list of broken images, and it takes 2 to 4 hours.
- **Risk:**
  - **`ctr images ls`: none.** It is a list command from your rule; sudo asks for your password.
  - **The start test: low.** Each test container runs `true` and is removed at once (`--rm`), so it touches no other container, and it never removes an image. Like any `docker run`, it may unpack an image that wasn't unpacked yet. That adds files and changes nothing that exists.

```bash
cd ~/snapshot-audit/inventory
sudo ctr -n moby images ls -q > images_list.txt                       # every image name
wc -l < images_list.txt
nohup nice bash ~/snapshot-audit/layer_test.sh images_list.txt layer_test.tsv > layer_test.log 2>&1 &
```

- **Expected:** about `7089`, then a job number and a process ID. `wc -l < ~/snapshot-audit/inventory/layer_test.tsv` grows as it runs; it is finished when `pgrep -f layer_test.sh` prints nothing. You get the results in step 7.2.
- **Send me:** the image count.

## Step 4: the logs (30 minutes, no sudo)

### 4.1 The boots, and the order of `/data` and containerd (Q1, Q5)

- **Why:** this is the heart of the 12 Oct event. If containerd started before `/data` was mounted, it worked on the empty folder of the system disk. `systemd[1]` lines are systemd starting and stopping services and mounts; the filter keeps only those.
- **Risk:** none. It reads the journal.

```bash
cd ~/snapshot-audit/logs
journalctl --list-boots --no-pager > boots.txt                         # every boot the journal knows
for b in -1 0; do                                                       # -1 is the 11 Sep boot, 0 is the 12 Oct boot
  echo "===== boot $b: systemd starting and stopping data.mount, containerd and docker"
  journalctl -b "$b" -o short-iso --no-pager -u data.mount -u containerd.service -u docker.service | grep -m 12 -E 'systemd\[1\]: .*(Mount|Start|Stopp|Fail)'
  echo "-- last lines of this boot"
  journalctl -b "$b" -o short-iso --no-pager -n 5 | cut -c1-160
done >> boots.txt 2>&1
cat boots.txt
```

- **Expected**, after about 2 minutes:
  - Three boots: 24 Jul 15:49, 11 Sep 09:14 and 12 Oct 10:38.
  - **Boot 0:** `Starting containerd.service` at 10:38:15, `Failed with result 'timeout'` at 10:39:45 and `Started containerd.service` at 10:40:15. Then `Mounted data.mount` at 10:54:05. That confirms Q1.
  - **Boot -1:** few or no lines, because its older entries were rotated away. Its last lines are from 11 Oct 15:24, with no shutdown messages.
- **Send me:** the output of `cat boots.txt`.

### 4.2 Out of memory, stuck processes and disk messages (Q9)

- **Why:**
  - **Out-of-memory kills:** when memory runs out, the kernel kills processes ("out-of-memory killer"). The 12 Oct restart followed a day of that.
  - **Stuck processes:** "blocked for more than" and "waiting for writeback" mean a process waited minutes for the disk.
  - **Disk recovery:** "recovery complete" and "orphan" mean a disk wasn't unmounted cleanly.
  - **The question:** did this also happen around 6 and 7 Oct, when the first damage appeared?
- **Risk:** none. It reads the kernel logs, about 50 MB.

```bash
cd ~/snapshot-audit/logs
zgrep -hE 'Out of memory: Killed process|invoked oom-killer|blocked for more than|waiting for writeback|soft lockup|I/O error|EXT4-fs (error|warning)|EXT4-fs \(nvme|recovery complete|orphan' \
  /var/log/kern.log.4.gz /var/log/kern.log.3.gz /var/log/kern.log.2.gz /var/log/kern.log.1 /var/log/kern.log > kernel.txt
grep 'Out of memory: Killed' kernel.txt | cut -c1-10 | uniq -c > oom_per_day.txt                   # kills per day
grep -oE 'Killed process [0-9]+ \([^)]+\)' kernel.txt | awk '{print $4}' | sort | uniq -c | sort -rn > oom_victims.txt
grep -E 'blocked for more than|waiting for writeback' kernel.txt | cut -c1-160 > hung_tasks.txt     # processes stuck on the disk
grep -E 'systemd-journald.service: (Failed|State)' /var/log/syslog > journald_watchdog.txt           # these lines are only in syslog
cat oom_per_day.txt oom_victims.txt journald_watchdog.txt
cut -c1-13 hung_tasks.txt | sort | uniq -c                                                          # stuck processes per hour
grep -E 'recovery complete|orphan|I/O error|EXT4-fs error' kernel.txt | cut -c1-160
```

- **Expected:**
  - **Kills per day:** on 11 Oct (from 15:21) and on 12 Oct (until 10:36), 26 in all in `kern.log`. Another day, above all 6 or 7 Oct, is a lead.
  - **Victims:** `python` and `java`, not `containerd` or `dockerd`.
  - **Watchdog:** failures at 15:23 and 15:31 on 11 Oct.
  - **Stuck processes:** at least `2026-10-14T20` and `2026-10-14T21`, when `dockerd` waited more than 2 minutes for the disk.
  - **Disk messages:**
    - `orphan cleanup` on `nvme0n1p3` at 10:38 on 12 Oct;
    - `recovery complete` and `1386 orphan inodes deleted` on `nvme1n1p1` at 10:54;
    - no `I/O error`.
- **Send me:** the whole output.

### 4.3 Logins and sudo commands since 13 Sep (Q2, Q8, Q11)

- **Why:** every SSH login is in `auth.log`, with the fingerprint of the key used. Root's work therefore shows as root logins, and as sudo commands when root used sudo. This is how we see who was on the host when fstab was edited and `/data` mounted.
- **Risk:** none. It reads `auth.log*`.

```bash
cd ~/snapshot-audit/logs
a="/var/log/auth.log.4.gz /var/log/auth.log.3.gz /var/log/auth.log.2.gz /var/log/auth.log.1 /var/log/auth.log"
zgrep -hE 'sshd.*Accepted ' $a > logins.txt                                           # every SSH login, with the key's fingerprint
zgrep -hE 'sudo.*COMMAND=' $a > sudo_commands.txt                                     # every sudo command
grep ' for root ' logins.txt | awk '{print substr($1, 1, 10), $NF}' | sort | uniq -c > root_logins_by_day.txt
grep -hE '^2026-10-12T(09|10|11|12):' logins.txt sudo_commands.txt | grep -E ' for root |COMMAND=' | sort > around_1012.txt
wc -l logins.txt sudo_commands.txt
cat root_logins_by_day.txt
echo "--- root logins and sudo commands, 12 Oct 09:00-12:59"; cat around_1012.txt
echo "--- all sudo commands"; cut -c1-200 sudo_commands.txt
echo "--- who logged in on 12 Oct 10:00-12:59"; grep -E '^2026-10-12T1[0-2]:' logins.txt | grep -oE 'for [a-z0-9_-]+' | sort | uniq -c
```

- **Expected:**
  - **Root logins per day:** the count, date and key fingerprint (`SHA256:…`) of each.
  - **12 Oct:** root logins between 10:38 and 12:10, when `/data` was mounted and fstab rewritten.
  - **Sudo commands:** a few lines, all by root, for example `systemctl start taskanchor-…` at 11:19 on 12 Oct, and the group change for `toby` at 10:17 on 15 Oct.
  - **Users:** the accounts that logged in on 12 Oct.
- **Send me:** the whole output. It contains IP addresses and user names; that's fine for this chat.

### 4.4 12 Oct in detail (Q4)

- **Why:** every start, stop and mount of containerd, Docker and `/data` on 12 Oct. The stop and start between 11:41 and 14:41 is when containerd switched back to the real database.
- **Risk:** none. It reads the journal.

```bash
cd ~/snapshot-audit/logs
journalctl --since '2026-10-12 10:30' --until '2026-10-12 15:00' -o short-iso --no-pager -u containerd -u docker -u data.mount > day_1012.txt
grep -E 'systemd\[1\]: .*(Start|Stopp|Mount|Fail)' day_1012.txt | cut -c1-160
```

- **Expected:**
  - containerd starting at 10:38 and 10:40, and `/data` mounted at 10:54;
  - Docker stopping at 11:48 and starting at 13:07;
  - a containerd stop and start between 11:41 and 14:41. Compare its time with the root logins in 4.3.
- **Send me:** the output.

### 4.5 containerd's restarts on 14 Oct (Q11)

- **Why:** containerd restarted at 11:11 and 17:36 on 14 Oct. A restart doesn't delete data, but we want to know who did it and why.
- **Risk:** none. It reads the journal and the files from 4.3.

```bash
cd ~/snapshot-audit/logs
journalctl -u containerd --since 2026-10-14 --until 2026-10-15 -o short-iso --no-pager | grep -E 'systemd\[1\]: ' > restarts_1014.txt
grep -hE '2026-10-14T(10:5|11:[01]|17:[23])' logins.txt sudo_commands.txt | grep -E ' for root |COMMAND=' >> restarts_1014.txt
cut -c1-160 restarts_1014.txt
```

- **Expected:** `Stopping`, `Stopped`, `Starting` and `Started` lines at about 11:11 and 17:36, and any root login or sudo command just before them.
- **Send me:** the output.

### 4.6 Your own scheduled jobs (Q8)

- **Why:** rule out your own account first. Four accounts keep background services running without being logged in: naiyu, toby, yun and zihan. Step 6.3 lists the other three.
- **Risk:** none. It only lists.

```bash
systemctl --user list-timers --all --no-pager
crontab -l
```

- **Expected:** no timer that deletes files, and only the hourly canary line in the crontab.
- **Send me:** the output.

## Step 5: containerd's records against the disk (20 minutes, sudo)

### 5.1 Save containerd's lists, and the files on disk

- **Why:** compare containerd's records with what is really on disk. `images check` tells, for each image, whether all its downloaded files are present (`complete`) and whether it's unpacked. The lists of layer folders and downloaded files are what's on disk.
- **Risk:** none. Every sudo command here is a list command from your rule. `images check` reads the records of all 7,089 images; it takes up to 10 minutes and changes nothing.

```bash
cd ~/snapshot-audit/inventory
d=/data/containerd
sudo ctr -n moby images check > images_check.txt 2>&1                 # each image: complete or incomplete, unpacked or not
sudo ctr -n moby snapshots ls > snapshots_db.txt                      # every layer record
sudo ctr -n moby content ls > content_records.txt                     # every downloaded-file record, with its age
sudo ctr -n moby content active > content_active.txt                  # downloads in progress, or cut off by the restart
sudo ls -l --full-time $d/io.containerd.snapshotter.v1.overlayfs/snapshots > snapshot_dirs.txt    # layer folders on disk
sudo ls -U $d/io.containerd.content.v1.content/blobs/sha256 > blob_files.txt                     # downloaded files on disk
wc -l images_check.txt snapshots_db.txt content_records.txt content_active.txt snapshot_dirs.txt blob_files.txt
awk 'NR > 1 {print $4, $NF}' images_check.txt | sort | uniq -c        # images per state
cat content_active.txt
```

- **Expected:**
  - `wc -l` gives many thousands of lines for each list, and few for `content_active.txt`.
  - The states are counts such as `5000 complete true` and `900 incomplete true`. "Incomplete" means downloaded files are missing.
  - `content_active.txt` is a header, and maybe a few downloads cut off on 11 or 12 Oct.
  - If a command says `not allowed` or `I'm sorry`, stop and send it to me: it doesn't match the rule.
- **Send me:** the whole output.

### 5.2 Records against disk, and the ages of the missing files (Q7)

- **Why:** this is the test that tells the remaining candidates apart (report, section 7.2).
  - **Deleted by age:** the missing files are all old, and the present ones younger.
  - **Lost in the restart:** the missing files are from 11 and 12 Oct.
  - **Neither:** no pattern.
  
  `comm` compares two sorted lists: `-23` keeps lines only in the first (recorded but not on disk), and `-13` lines only in the second (on disk but not recorded).
- **Risk:** none. No sudo; it reads the files from 5.1 and writes text files.

```bash
cd ~/snapshot-audit/inventory
echo "layer records $(($(wc -l < snapshots_db.txt) - 1)), layer folders $(grep -c '^d' snapshot_dirs.txt)"
awk 'NR > 1 {sub(/^sha256:/, "", $1); print $1}' content_records.txt | LC_ALL=C sort > blobs_db.txt
LC_ALL=C sort blob_files.txt > blob_files.sorted
LC_ALL=C comm -23 blobs_db.txt blob_files.sorted > blobs_missing.txt          # recorded, but not on disk
LC_ALL=C comm -13 blobs_db.txt blob_files.sorted > blobs_unrecorded.txt       # on disk, but not recorded
wc -l blobs_db.txt blobs_missing.txt blobs_unrecorded.txt
awk 'NR == FNR {miss["sha256:" $1]; next} FNR > 1 {print (($1 in miss) ? "missing" : "present") "\t" $3 " " $4}' \
  blobs_missing.txt content_records.txt | sort | uniq -c > blob_ages.txt
cat blob_ages.txt
```

- **Expected:**
  - More layer records than layer folders.
  - Some thousands of missing files, and few or no unrecorded ones.
  - `blob_ages.txt`: counts per state and age, for example `812 missing 2 months` and `40210 present 3 weeks`.
- **Send me:** the whole output.

### 5.3 Folders from a second numbering (Q5)

- **Why:** the empty database of 12 Oct numbered its folders from 1, while the real one is past 350,000. A low-numbered folder with another date would mean another split brain, for example at the 11 Sep boot.
- **Risk:** none. No sudo; it reads `snapshot_dirs.txt`.

```bash
cd ~/snapshot-audit/inventory
awk '$9 ~ /^[0-9]+$/ && $9 < 1000 {print $9, $6, $7, $8}' snapshot_dirs.txt | sort -n > low_numbered_dirs.txt
wc -l < low_numbered_dirs.txt
awk '{print $2}' low_numbered_dirs.txt | sort | uniq -c                       # low-numbered folders per date
head -n 5 low_numbered_dirs.txt
```

- **Expected:** only the date `2026-10-12`, with times between 10:54 and 14:41, including folders 37 and 54. Any other date answers Q5 with "yes".
- **Send me:** the output.
- **Result on 15 Oct, 13:37:** the count was `0`, and `head` printed nothing. That is not the expected list, so 5.3b checks whether field 9 is really the folder name.

### 5.3b Check the columns of `snapshot_dirs.txt` (Q5)

- **Why:** step 5.3 printed 0. That is either true (no folder numbered under 1000) or a wrong column (field 9 is not the folder name, so every line was skipped). This prints one line field by field, counts names under 1000 using the last field, and lists the ten lowest folder numbers.
- **Risk:** none. No sudo; it reads `snapshot_dirs.txt`.

```bash
cd ~/snapshot-audit/inventory
awk 'NR==2 {print NF; for (i=1;i<=NF;i++) print i, $i}' snapshot_dirs.txt
echo "---"
awk 'NR>1 && $NF ~ /^[0-9]+$/ && $NF < 1000 {c++} END {print "under 1000:", c+0}' snapshot_dirs.txt
awk 'NR>1 && $NF ~ /^[0-9]+$/ {print $NF}' snapshot_dirs.txt | sort -n | head
```

- **Expected:** 9 fields, with field 9 a folder number. `under 1000: 0`. The ten lowest numbers start at 152997.
- **Send me:** the output.
- **Result on 15 Oct, 13:39:** field 9 is the folder name (`152997`, dated `2026-09-10`). `under 1000: 0`. The lowest folders are 152997, 153000, 153120, 153125, 153126, 153127, 153129, 153130, 153132, 153133. The empty list from 5.3 is real. Folders 37 and 54 are gone, so Q5 cannot be answered from folder numbers.

## Step 6: what only root can read (20 minutes, sudo)

### 6.1 Whose keys may log in as root (Q2)

- **Why:** match the fingerprints of the root logins in 4.3 to the people the keys belong to. Each key's comment is usually its owner's name or computer.
- **Risk:** none. It prints fingerprints and comments, not the keys.

```bash
cd ~/snapshot-audit/logs
sudo ssh-keygen -lf /root/.ssh/authorized_keys > root_keys.txt
cat root_keys.txt
```

- **Expected:** one line per key, for example `2048 SHA256:… comment (RSA)`. Your fingerprint from 0.2 should be among them, and so should each fingerprint in `root_logins_by_day.txt`.
- **Send me:** the output. You may replace e-mail addresses in the comments with names.

### 6.2 Root's shell history (Q2, Q8)

- **Why:** find any command of root's that edited fstab, mounted or unmounted, or deleted under `/data`. Only matching lines are kept. `-B1` also keeps the line before each match; if bash saved timestamps, that line is `#` followed by seconds since 1970.
- **Risk:** none. `sudo cat` only reads the file; the filtering runs as `toby`.

```bash
cd ~/snapshot-audit/logs
sudo cat /root/.bash_history | grep -nE -B1 'fstab|mount|/data|containerd|docker|rm -|find |prune' > root_history.txt
wc -l < root_history.txt
cat root_history.txt
```

- **Expected:** the commands that wrote and fixed fstab and mounted `/data`, and any deletion under `/data`. To turn a timestamp line such as `#1791979238` into a date, run `date -d @1791979238`. Ubuntu's default keeps only the last 2,000 commands, so a missing line isn't proof.
- **Send me:** the output. Read it first, and replace any password or token with `<removed>`.

### 6.3 Every crontab, and the other users' timers (Q8)

- **Why:** find any scheduled job, of any user, that deletes files or prunes Docker. Crontabs are in `/var/spool/cron/crontabs/<user>`. User timers are in `~/.config/systemd/user/` of the three users who keep services running.
- **Risk:** none. It reads crontabs, and lists only the file names of the timers.

```bash
cd ~/snapshot-audit/logs
sudo grep -rH . /var/spool/cron/crontabs | grep -vE ':[[:space:]]*#' > crontabs.txt
sudo ls -laR /home/naiyu/.config/systemd/user /home/yun/.config/systemd/user /home/zihan/.config/systemd/user > user_timers.txt 2>&1
echo "--- crontab lines that delete, prune or touch Docker or /data"; grep -iE 'rm |find |prune|delete|docker|ctr|/data' crontabs.txt
echo "--- user units"; grep -E '\.(timer|service)' user_timers.txt
```

- **Expected:**
  - Crontab lines as `/var/spool/cron/crontabs/<user>:<job>`, including your canary.
  - Unit file names of naiyu, yun and zihan; zihan's include `contextgraph-release-…` services, which use `/data`.
  - A timer named like clean, prune or gc is a lead; its owner should say what it does.
- **Send me:** the output.

### 6.4 The empty store hidden under `/data` (Q3)

- **Why:** when `/data` is mounted, the folder of the same name on the system disk is hidden underneath. containerd's empty database from 12 Oct 10:38 should still be there. The only way to look under a mount point is a second, read-only view of the system disk at `/mnt/rootfs`.
- **Risk:** low.
  - The view is read-only (`ro`), so nothing can be written through it, not even by root.
  - It covers the system disk only, not `/data`, and it changes no file.
  - The last commands remove it.
  - `findmnt` checks that the view really is read-only. **If it shows `rw`, stop:** run only `sudo umount /mnt/rootfs`, and send me the output.

```bash
cd ~/snapshot-audit/logs
sudo mkdir -p /mnt/rootfs                                  # an empty folder for the view
sudo mount -o bind,ro / /mnt/rootfs                        # the read-only view of the system disk
findmnt -no TARGET,OPTIONS /mnt/rootfs                     # must show "/mnt/rootfs ro,..."
sudo ls -la --full-time /mnt/rootfs/data /mnt/rootfs/data/containerd /mnt/rootfs/data/containerd/io.containerd.metadata.v1.bolt > hidden_store.txt 2>&1
sudo du -sh /mnt/rootfs/data >> hidden_store.txt 2>&1      # how much is hidden
sudo umount /mnt/rootfs                                    # remove the view
findmnt /mnt/rootfs || echo "view removed"
cat hidden_store.txt
```

- **Expected:**
  - The first `findmnt` shows `/mnt/rootfs ro,…`.
  - `hidden_store.txt` shows a `containerd` folder from 2026-10-12, around 10:38, and a `meta.db` last changed between 10:38 and 14:41. Together they prove the split brain.
  - The last `findmnt` prints `view removed`.
  
  An empty `/mnt/rootfs/data` would mean someone deleted the hidden store later; we'd ask root.
- **Send me:** the whole output.

### 6.5 The two containers from the split brain (Q12)

- **Why:** two containers were created on 12 Oct at 11:33 and 11:41, while containerd used the empty database. If the real database doesn't know them, they will fail at their next start, and their owners should be told.
- **Risk:** none. `docker inspect` and the `sudo ls` only read, and the database check reads the file from 5.1.

```bash
cd ~/snapshot-audit/logs
for c in f870b22117e6 6afbbec81e30; do
  id=$(docker inspect --format '{{.Id}}' "$c")                                       # full container ID
  echo "== $c $(docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.Status}}' "$c")"
  echo "records for it in the real database: $(grep -c "$id" ../inventory/snapshots_db.txt)"
done > split_brain_containers.txt
sudo ls -ld --full-time /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/37 /data/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/54 >> split_brain_containers.txt 2>&1
cat split_brain_containers.txt
```

- **Expected:** for each container, its name, image and state (`running` or `exited`), and its number of records.
  - **2:** its layer and init records exist, so it's known.
  - **0:** it isn't known.
  
  Then both folders, dated 12 Oct.
- **Send me:** the output.

## Step 7: results of the long jobs

### 7.1 The slow log scans (Q6, Q10)

- **When:** `cat ~/snapshot-audit/logs/long_scans.log` shows `Q10 done`.
- **Why:**
  - `damage_first_seen.txt` dates the first appearance of each kind of error.
  - `syslog_volume.txt` shows which days and programs wrote the 15 GB of syslog.
- **Risk:** none.

```bash
cd ~/snapshot-audit/logs
cat long_scans.log damage_first_seen.txt
grep '^day' syslog_volume.txt | sort                                  # syslog size per day, 4 to 11 Oct
grep '^prog' syslog_volume.txt | sort -k2,2nr | head -n 15             # the programs that wrote the most lines
```

- **Expected:**
  - For each error type, the first containerd or Docker line with it, or `never`. Errors that a client sees aren't always in the daemon's log, so `never` doesn't prove anything.
  - One line per day with its size in MB, then the top 15 programs.
- **Send me:** the whole output.

### 7.2 The start test (impact)

- **When:** `pgrep -f layer_test.sh` prints nothing.
- **Why:** count the broken images per type of damage, see whose they are (grouped by repository), and list the containers that use a broken image.
- **Risk:** none. `docker ps` only lists.

```bash
cd ~/snapshot-audit/inventory
cut -f1 layer_test.tsv | sort | uniq -c                                              # images per result type
awk -F'\t' '$1 != "ok" {print $1 "\t" $2}' layer_test.tsv | sed -E 's|[:@][^/]*$||' | sort | uniq -c | sort -rn > broken_by_repo.txt
awk -F'\t' '$1 != "ok" {print $2}' layer_test.tsv | while read -r i; do
  docker ps -a --filter ancestor="$i" --format "{{.ID}}\t{{.Names}}\t{{.Status}}\t$i"
done > containers_on_broken.tsv
head -n 20 broken_by_repo.txt
wc -l < containers_on_broken.tsv
```

- **Expected:** counts of `ok`, `layers_missing`, `blob_missing`, `stale_record` and `other`. The DeepSWE repository should be near the top of the list, with 113.
- **Send me:** the whole output.

| Type | Meaning |
|---|---|
| `ok` | The image starts |
| `layers_missing` | A layer folder is gone, as with DeepSWE |
| `blob_missing` | A downloaded file is gone |
| `stale_record` | `already exists` or `missing parent` |
| `other` | Any other error, for example an image for another CPU type |

## Step 8: finish

### 8.1 Stop recording

- **Risk:** none. If bash says `There are running jobs`, type `exit` again; the `nohup` jobs keep running.

```bash
exit
```

- **Expected:** the normal prompt again. The recording is in `~/snapshot-audit/sessions/`.

### 8.2 Copy the evidence to your PC (optional)

- **Where:** terminal 2, your PC.
- **Why:** a copy for the meeting with the mentor. It goes outside the mac-k3d repository, because it contains user names, IP addresses and root's commands.
- **Risk:** none. It reads from the worker and writes on your PC.

```bash
mkdir -p ~/cloud-worker-evidence
scp -r toby@47.84.16.18:snapshot-audit/logs ~/cloud-worker-evidence/
scp -r toby@47.84.16.18:snapshot-audit/inventory ~/cloud-worker-evidence/
du -sh ~/cloud-worker-evidence/*
```

- **Expected:** two folders, `logs` and `inventory`, a few hundred MB together.

### 8.3 When the investigation is over: root removes the access

Root runs this, or tells you to run it with `root.pem` in the same way as step 1:

```bash
rm -f /etc/sudoers.d/90-toby-investigate                   # removes the rule
systemctl stop toby-investigate-expire.timer 2>/dev/null   # stops the backstop timer
passwd -l toby                                             # locks toby's password again
sudo -l -U toby                                            # shows that toby may not run sudo
```

- **Risk:** low. Each line only takes back what step 1 gave.
- **Expected:** the last line says that `toby` may not run sudo.

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `Permission denied (publickey)` with `root.pem` | The key isn't accepted for root on this host | Stop. Give root the fingerprint from 0.2 |
| `UNPROTECTED PRIVATE KEY FILE` | Others can read the key | Run step 0.1 again |
| `I'm sorry toby` or `not allowed` after a sudo command | The command differs from the rule, even by one character | Copy the command again exactly. If it still fails, send it to me |
| `sudo: … incorrect password` | Typing mistake | Try again. After three failures, wait a minute |
| The connection drops | Nothing is lost; `nohup` jobs keep running | Log in, run step 2.3 again, and carry on |
| `findmnt` shows `rw` in step 6.4 | The view isn't read-only | Run only `sudo umount /mnt/rootfs`, then send me the output |
| `No space left on device` | The system disk is full | Stop, and send me `df -h / /home` |
| Anything else unexpected | | Stop, and send me the command and its output |
