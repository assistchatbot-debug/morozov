#!/bin/bash
echo "================================================================================"
echo "  АВТОМАТИЧЕСКОЕ СОПОСТАВЛЕНИЕ ТОВАРОВ"
echo "================================================================================"
curl -s "https://hb-tech.bitrix24.kz/rest/34137/03i76p52if8jjq3a/crm.product.list" -d 'select[]=ID&select[]=NAME&order[ID]=ASC' | python3 -c "
import json, sys, re
from difflib import SequenceMatcher

data = json.load(sys.stdin)
b24 = data.get('result', [])

with open('1c_nomenclature.txt', 'r', encoding='utf-8') as f:
    onec = [line.strip() for line in f if line.strip()]

def sim(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def code(name):
    for p in [r'RT[\s-]?(\d+)', r'BTH-[\d]+T', r'GS-[\d]+', r'\d{5,}']:
        m = re.search(p, name, re.I)
        if m: return m.group(0).upper().replace(' ', '')
    return '-'.join(name.split()[:3]).upper()[:20]

print('Сопоставление...\n')
matched = 0
with open('mapping.csv', 'w', encoding='utf-8') as f:
    f.write('bitrix24_id,bitrix24_name,onec_code,onec_name\n')
    for p in b24:
        best, score = None, 0
        for o in onec:
            s = sim(p['NAME'], o)
            if s > score: best, score = o, s
        if score >= 0.6:
            f.write(f'\"{p[\"ID\"]}\",\"{p[\"NAME\"]}\",\"{code(best)}\",\"{best}\"\n')
            matched += 1
            print(f\"{'✅' if score>0.8 else '⚠️'} {p['ID']:>4} {score:.0%} {p['NAME'][:45]}\")
print(f'\n✅ Сопоставлено: {matched}/{len(b24)}\n')
"
echo "Загрузка в БД..."
tail -n +2 mapping.csv | while IFS=',' read -r id name code onec; do
    id=$(echo $id | tr -d '"'); code=$(echo $code | tr -d '"')
    name=$(echo $name | tr -d '"'); onec=$(echo $onec | tr -d '"')
    curl -s -X POST https://bizdnai.com/morozov/api/mapping/product -H "Content-Type: application/json" -d "{\"bitrix24_product_id\":\"$id\",\"bitrix24_product_name\":\"$name\",\"onec_product_code\":\"$code\",\"onec_product_name\":\"$onec\"}" >/dev/null && echo "✅ $id → $code"
done
echo ""; echo "✅ ГОТОВО!"
