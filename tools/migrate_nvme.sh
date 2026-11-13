#!/bin/bash
# /data 를 SD 카드에서 NVMe(SK hynix BC511 512GB)로 이전. 실행: sudo bash migrate_nvme.sh
set -euo pipefail
DEV=/dev/nvme0n1
PART=${DEV}p1
log() { echo "[$(date +%H:%M:%S)] $*"; }

log "0. guards"
model=$(tr -d ' ' < /sys/class/nvme/nvme0/model)
[[ "$model" == HFM512GDJTNI* ]] || { echo "ABORT: unexpected model $model"; exit 1; }
findmnt -no SOURCE / | grep -q mmcblk0p1 || { echo "ABORT: root is not on SD"; exit 1; }
mountpoint -q /data && { echo "ABORT: /data already a mountpoint"; exit 1; }
[ -f /data/docker-compose.yml ] || { echo "ABORT: /data/docker-compose.yml missing"; exit 1; }
size=$(lsblk -bdno SIZE "$DEV"); [ "$size" -gt 400000000000 ] || { echo "ABORT: size $size"; exit 1; }
log "   model=$model size=$size OK"

log "1. unmount any nvme partitions (udisks automount)"
for p in "$DEV"p*; do
  if findmnt -rn -S "$p" >/dev/null 2>&1; then umount "$p" 2>/dev/null || umount -l "$p"; log "   unmounted $p"; fi
done

log "2. stop containers and docker"
cd /data && docker compose down --timeout 30
systemctl stop docker.socket docker.service
sleep 2
if mount | grep -q " /data/docker"; then echo "ABORT: overlay still mounted under /data/docker"; exit 1; fi

log "3. wipe, partition (GPT, 1 partition), ext4"
sgdisk --zap-all "$DEV" >/dev/null
wipefs -a "$DEV" >/dev/null
parted -s "$DEV" mklabel gpt mkpart data ext4 1MiB 100%
partprobe "$DEV"; sleep 2; udevadm settle
mkfs.ext4 -F -q -L data -m 1 "$PART"
mkdir -p /mnt/nvme
mount -o noatime "$PART" /mnt/nvme
log "   $(findmnt -no SOURCE,FSTYPE,SIZE /mnt/nvme)"

log "4. copy /data -> nvme (rsync, owners/acl/xattr preserved)"
rsync -aHAX -x --numeric-ids /data/ /mnt/nvme/
sync

log "5. verify"
src=$(find /data -xdev | wc -l)
dst=$(find /mnt/nvme -xdev -not -path "/mnt/nvme/lost+found*" | wc -l)
log "   entries src=$src dst=$dst"
[ "$src" -eq "$dst" ] || { echo "ABORT: entry count mismatch"; exit 1; }
du -sh /data /mnt/nvme | sed 's/^/   /'

log "6. swap: keep SD copy as /data.sd, mount nvme at /data"
umount /mnt/nvme
mv /data /data.sd
mkdir /data
UUID=$(blkid -s UUID -o value "$PART")
cp /etc/fstab "/etc/fstab.bak-$(date +%Y%m%d)"
echo "UUID=$UUID  /data  ext4  defaults,noatime,nofail,x-systemd.device-timeout=15  0  2" >> /etc/fstab
mount /data
log "   $(findmnt -no SOURCE,TARGET,FSTYPE,OPTIONS /data)"

log "7. docker must wait for /data mount"
mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nRequiresMountsFor=/data\n' > /etc/systemd/system/docker.service.d/10-data-mount.conf
systemctl daemon-reload
systemctl start docker
cd /data && docker compose up -d
sleep 20
docker compose ps --format "table {{.Name}}\t{{.Status}}"
log "MIGRATION DONE"
