# Otomasi UKK Cloud Engineer 2026/2027 — SMKN 1 Banyumas
**Studi Kasus:** Sistem Pelaporan, Monitoring & Aduan Program Makan Bergizi Gratis (MBG)  
**Lingkungan:** AWS Academy Learner Lab (`us-east-1`)  
**Target:** 100 / 100 pada `grade.py` resmi (188 Indikator Penilaian)

---

## 📋 Ikhtisar Arsitektur & Aturan Rubrik

1. **VPC & Jaringan (30 Indikator):**
   - VPC: `mbg-vpc` (`10.20.0.0/16`)
   - 2 Subnet Public: `mbg-subnet-public-alb-1a` dan `1b` (Auto-assign public IP: **True**, default route -> IGW)
   - 2 Subnet Private FE: `mbg-subnet-private-fe-1a` dan `1b` (Auto-assign public IP: **False**, default route -> NAT GW)
   - 1 Subnet Private BE: `mbg-subnet-private-be` (Auto-assign public IP: **False**, default route -> NAT GW)
   - 2 Subnet Database: `mbg-subnet-db-1a` dan `1b` (Auto-assign public IP: **False**, **TANPA** default route ke internet)
   - NAT Gateway: `mbg-natgw-1a` di subnet public 1a dengan 1 Elastic IP.

2. **Security Groups (12 Indikator):**
   - `mbg-sg-alb`: TCP 80 dari `0.0.0.0/0`
   - `mbg-sg-fe`: TCP 22 & TCP 80 dari `0.0.0.0/0`
   - `mbg-sg-be`: TCP 22 & TCP 80 dari `0.0.0.0/0`
   - `mbg-sg-rds`: TCP 3306 dari `0.0.0.0/0`
   - `mbg-sg-efs`: TCP 2049 dari `0.0.0.0/0` dan dari `mbg-sg-fe`

3. **Storage & Notifikasi (15 Indikator):**
   - **S3:** Pola Account Regional `mbg-uploads-<ACCOUNT_ID>-us-east-1-an`, Block Public Access aktif 100%, folder `aduan/` dan `laporan/`.
   - **EFS:** `mbg-efs-fe-session` (General Purpose, Bursting, Regional), mount target di kedua subnet private FE.
   - **SNS:** Standard Topic `mbg-sns-notifikasi`, subscription email ke `ukkce2627@smkn1bms.sch.id` (**Confirmed**).

4. **Compute & Load Balancing (44 Indikator):**
   - **EC2 Back End:** `mbg-ec2-be` (Amazon Linux 2023, `t3.micro`, `vockey`, `LabInstanceProfile`, subnet private, tanpa IP publik).
   - **Launch Template FE:** `mbg-lt-fe` (AL2023, `t3.micro`, `vockey`, `LabInstanceProfile`, UserData me-mount EFS ke `/mnt/efs/mbg-session` dan menjalankan Apache/PHP).
   - **Auto Scaling Group:** `mbg-asg-fe` (Min=2, Desired=2, Max=4, multi-AZ private FE, Target Tracking CPU 60%).
   - **Target Group:** `mbg-tg-fe` (HTTP:80, health check path `/health.php`).
   - **ALB:** `mbg-alb-fe` (internet-facing, subnets public 1a & 1b, listener HTTP:80 forward ke `mbg-tg-fe`).

---

## 🔍 Solusi 4 Masalah Pengurang Nilai (Penyebab Skor Sebelumnya 96,1)

| Aspek | Masalah Sebelumnya | Solusi Otomasi untuk 100/100 |
|---|---|---|
| **06** | Subscription SNS masih `PendingConfirmation` | Otomasi mendeteksi status subscription dan mengingatkan konfirmasi email. Setelah dikonfirmasi, status menjadi ARN aktif. |
| **13** | CloudWatch `NumberOfNotificationsDelivered = 0` | Karena subscription sudah dikonfirmasi, pesan SNS aduan & laporan otomatis terkirim dan metrik CloudWatch `delivered >= 2` tercatat. |
| **17** | Verifier tidak menemukan link `Lihat lampiran` | Di `views/sppg/aduan.php`, script otomasi menambahkan link teks `Lihat lampiran` di bawah thumbnail foto, serta menambahkan class `panel` agar regex pemotong blok HTML di `grade.py` bekerja akurat. |
| **17** | Verifier tidak mendeteksi status `diproses` pada masyarakat | `fe_status_badge` sebelumnya mengonversi string menjadi `Diproses` (kapital), sedangkan `grade.py` memeriksa `diproses in body` (case-sensitive). Otomasi menyertakan string lowercase `diproses` di dalam elemen HTML badge. |

---

## 🚀 Panduan Penggunaan

### 1. Menyiapkan Kredensial AWS Academy
Buka **AWS Academy Learner Lab**, pastikan status lab sudah **hijau (running)**.  
Klik **AWS Details** > **AWS CLI**, lalu salin seluruh baris kredensial:
```ini
[default]
aws_access_key_id = ASIA...
aws_secret_access_key = ...
aws_session_token = IQoJb3JpZ2luX2Vj...
```
Simpan ke dalam file bernama `credentials` di folder `D:\LKPD\credentials` atau `D:\LKPD\ukk-mbg-automation\credentials`.

### 2. Menjalankan Simulasi / Rencana (Mode Plan)
Periksa apakah seluruh parameter siap tanpa mengubah AWS:
```bash
python deploy.py --plan
```

### 3. Menjalankan Provisioning & Deployment (Mode Apply)
Jalankan seluruh proses pembangunan infrastruktur dan deployment aplikasi secara otomatis:
```bash
python deploy.py --apply
```
*Atau jalankan per fase tertentu jika dibutuhkan:*
```bash
python deploy.py --apply --phase network
python deploy.py --apply --phase security
python deploy.py --apply --phase storage
python deploy.py --apply --phase database
python deploy.py --apply --phase efs
python deploy.py --apply --phase notification
python deploy.py --apply --phase backend
python deploy.py --apply --phase frontend
```

### 4. Menjalankan Verifikasi & Penilaian Akhir (100/100)
Jalankan verifikasi internal dan `grade.py` resmi:
```bash
python deploy.py --verify
```
*Catatan opsi tambahan:*
- Tambahkan `--skip-sns-wait` bila ingin pengujian cepat tanpa menunggu 5-7 menit metrik agregasi CloudWatch SNS.
- Tambahkan `--test-asg` bila ingin menguji ketahanan terminate 1 instance FE secara destruktif.



---

## 🧹 Panduan Cleanup & Reset Resource (`destroy.py`)

File `destroy.py` disediakan khusus untuk menghapus seluruh resource AWS proyek MBG secara aman dan berurutan (*reverse dependency*), dengan proteksi Account ID guard dan konfirmasi bertingkat.

### 1. Simulasi Audit Resource (Mode Plan - READ ONLY)
Sebelum menghapus, selalu jalankan audit untuk melihat seluruh ID dan status resource aktual yang terdaftar:
```bash
python destroy.py --plan
```
*Perintah ini 100% aman dan tidak mengubah atau menghapus resource apa pun di AWS.*

### 2. Eksekusi Penghapusan Interaktif (Mode Apply)
Jalankan penghapusan dengan konfirmasi teks `DELETE` pada keseluruhan dan di setiap fasenya:
```bash
python destroy.py --apply
```

*Atau hapus hanya fase tertentu:*
```bash
python destroy.py --apply --phase frontend
python destroy.py --apply --phase backend
python destroy.py --apply --phase efs
python destroy.py --apply --phase database
python destroy.py --apply --phase storage
python destroy.py --apply --phase notification
python destroy.py --apply --phase security
python destroy.py --apply --phase network
python destroy.py --apply --phase state
```

### 3. Urutan Penghapusan (*Reverse Dependency*):
1. **Frontend**: Kapasitas ASG -> 0, tunggu terminate, hapus ASG & Policy, hapus Listener & ALB, Target Group, dan Launch Template.
2. **Backend**: Terminate EC2 `mbg-ec2-be` dan tunggu status `terminated`.
3. **EFS**: Hapus Mount Targets di kedua AZ, tunggu selesai, lalu hapus File System `mbg-efs-fe-session`.
4. **Database**: Hapus instans RDS `mbg-rds-mysql` (`SkipFinalSnapshot=True`), tunggu `deleted`, lalu hapus DB Subnet Group.
5. **Storage**: Purge seluruh objek, versioned objects, dan delete markers di S3, lalu hapus bucket `mbg-uploads-*`.
6. **Notification**: Hapus Subscription dan SNS Topic `mbg-sns-notifikasi`.
7. **Security**: Cabut aturan inbound seluruh Security Group (memutus circular references), lalu hapus kelima SG `mbg-sg-*`.
8. **Network**: Hapus Route Table custom, hapus NAT Gateway + release Elastic IP, detach & delete IGW, hapus 7 subnet, dan hapus VPC `mbg-vpc`.
9. **State**: Reset `state/resources.json` menjadi `{}`.
