with open('D:/LKPD/ukk-mbg-automation/modules/compute_fe.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Locate line 150
start_idx = -1
end_idx = -1
for i, l in enumerate(lines):
    if '# Terapkan patch kompatibilitas rubrik grade.py:' in l:
        start_idx = i
    if '# Tulis config.php Front End' in l:
        end_idx = i
        break

print(f'Replacing lines {start_idx} to {end_idx}')
new_lines = [
    "# Terapkan patch kompatibilitas rubrik grade.py via python\n",
    "python3 - << 'PYEOF'\n",
    "p = 'views/sppg/aduan.php'\n",
    "with open(p, 'r', encoding='utf-8') as f:\n",
    "    c = f.read()\n",
    "if 'panel' not in c:\n",
    "    c = c.replace('class=\"card shadow-sm mb-3\"', 'class=\"card shadow-sm mb-3 panel\"')\n",
    "if 'Lihat lampiran' not in c:\n",
    "    t = '<div class=\"form-text\">Klik foto untuk memperbesar.</div>'\n",
    "    r = '<div><a href=\"<?= fe_e($a[\\x27file_url\\x27]) ?>\" target=\"_blank\" rel=\"noopener\">Lihat lampiran</a></div>\\n<div class=\"form-text\">Klik foto untuk memperbesar.</div>'\n",
    "    c = c.replace(t, r)\n",
    "with open(p, 'w', encoding='utf-8') as f:\n",
    "    f.write(c)\n",
    "\n",
    "h = 'includes/helpers.php'\n",
    "with open(h, 'r', encoding='utf-8') as f:\n",
    "    c = f.read()\n",
    "if 'd-none' not in c:\n",
    "    old = 'fe_e(ucfirst($status))'\n",
    "    new = 'fe_e(ucfirst($status)) . \\x27<span class=\"d-none\"> \\x27 . fe_e($status) . \\x27</span>\\x27'\n",
    "    c = c.replace(old, new)\n",
    "with open(h, 'w', encoding='utf-8') as f:\n",
    "    f.write(c)\n",
    "PYEOF\n\n"
]

lines[start_idx:end_idx] = new_lines

with open('D:/LKPD/ukk-mbg-automation/modules/compute_fe.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('Done!')

