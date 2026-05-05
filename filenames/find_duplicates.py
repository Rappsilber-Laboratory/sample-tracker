import os
import pandas as pd
from collections import defaultdict

df = pd.read_csv('filenames/address_book.csv')
ms = df[df['location'].str.contains('/MS_Data/', na=False)].copy()
ms['directory'] = ms['location'].map(os.path.dirname)

dir_files = defaultdict(set)
dir_size = defaultdict(float)
for _, row in ms.iterrows():
    d = row['directory']
    dir_files[d].add(row['file_name'])
    dir_size[d] += row['size_GB']

buckets = defaultdict(list)
for d, files in dir_files.items():
    buckets[frozenset(files)].append(d)

def priority(path):
    if '#recycle' in path:
        return 0
    if 'backup' in path.lower():
        return 1
    return 2

rows = []
for dirs in buckets.values():
    if len(dirs) < 2:
        continue
    dirs = sorted(dirs)
    for i in range(len(dirs)):
        for j in range(i + 1, len(dirs)):
            a, b = dirs[i], dirs[j]
            size = dir_size[a]
            if b.startswith(a + '/'):
                col1, col2 = b, a
            elif a.startswith(b + '/'):
                col1, col2 = a, b
            else:
                col1, col2 = (a, b) if priority(a) <= priority(b) else (b, a)
            rows.append({
                'recommended_delete': col1,
                'copy_location': col2,
                'disk_space_GB': round(size / 1e9, 3),
            })

out = pd.DataFrame(rows).sort_values('disk_space_GB', ascending=False)
out.to_csv('filenames/duplicate_directories.csv', index=False)
print(f"Found {len(out)} duplicate directory pairs")
print(out.head(10).to_string(index=False))
