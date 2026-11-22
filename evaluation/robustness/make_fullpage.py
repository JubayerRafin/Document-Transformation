"""Full-page test documents in all 11 website languages (never for tuning on the held-out set).

    python -m evaluation.robustness.make_fullpage OUT_DIR [--set dev|heldout] [--langs en,ko,...]
        [--edge PATH_TO_BROWSER]

Every page is full: each document's HTML holds more text than its pages, a Chromium-based browser
prints it (real line breaking, fonts and shaping for every script), and the PDF is cut to whole pages
(a last, partly filled page is dropped). Per source language:

  report    2 pages: title band, headings, paragraphs, bullet list, table, warning box, two columns
  invoice   1 page:  address grid, 12-row table, totals, terms, notes box
  manual    2 pages: numbered steps, warning boxes, a picture with text in its pixels, a spec table
  news      2 pages: two-column newsletter with headings, quotes and a table
  scan      1 page:  the invoice rendered, tilted, noisy and saved as JPEG (no text layer)

The sentences are the same twelve statements in each language, written for this corpus (not machine
translated), so a translation into English can be checked for meaning (KEYS). Codes, dates, amounts,
emails and phone numbers are random per seed. manifest.json lists every file, its language, page
count and the sentences it contains. `--set heldout` uses other seeds: measure on it, never fix
against its individual files. `--set competition` is a third, separate set for demos.
"""
import argparse
import io
import json
import random
import shutil
import string
import subprocess
import tempfile
import time
from pathlib import Path

import pymupdf

LANGS = ['en', 'ko', 'ja', 'zh', 'ar', 'hi', 'bn', 'fr', 'de', 'es', 'pt']

# Twelve statements, in the same order in every language. {pct} and {n} are random numbers.
SENTENCES = {
    'en': ['Revenue rose by {pct} percent this quarter, mainly because of new contracts in the logistics segment.',
           'All shipments must be inspected on arrival, and any damage must be reported within two business days.',
           'The committee recommends extending the pilot programme to three more regional offices.',
           'Employees must complete the annual security training before the end of the fiscal year.',
           'Customer satisfaction rose for the third quarter in a row and reached a record high.',
           'Any change to the agreement must be made in writing and signed by both parties.',
           'The new warehouse will open next spring and employ about {n} people.',
           'Energy costs were higher than planned, but savings in transport offset most of the increase.',
           'Please keep this document and contact the service desk if any details are incorrect.',
           'The device must be stored in a dry place at a temperature between 5 and 30 degrees.',
           'Maintenance on the main production line is scheduled for the first weekend of each month.',
           'The audit found no material weaknesses, and all recommendations from last year were implemented.'],
    'ko': ['이번 분기 매출은 물류 부문의 신규 계약에 힘입어 {pct}% 증가했습니다.',
           '모든 화물은 도착 즉시 검사해야 하며, 손상이 있으면 영업일 기준 2일 이내에 보고해야 합니다.',
           '위원회는 시범 사업을 지역 사무소 세 곳으로 더 확대할 것을 권고합니다.',
           '직원은 회계연도가 끝나기 전에 연례 보안 교육을 이수해야 합니다.',
           '고객 만족도는 3분기 연속 상승하여 사상 최고치를 기록했습니다.',
           '계약의 모든 변경 사항은 서면으로 작성하고 양 당사자가 서명해야 합니다.',
           '새 물류창고는 내년 봄에 문을 열 예정이며 약 {n}명을 고용할 계획입니다.',
           '에너지 비용은 계획보다 높았지만, 운송비 절감으로 증가분의 대부분을 상쇄했습니다.',
           '이 문서를 보관하시고, 내용에 오류가 있으면 서비스 데스크로 연락해 주십시오.',
           '장치는 5도에서 30도 사이의 건조한 장소에 보관해야 합니다.',
           '주 생산 라인의 정기 점검은 매월 첫째 주말로 예정되어 있습니다.',
           '감사 결과 중대한 취약점은 발견되지 않았으며, 작년의 권고 사항은 모두 이행되었습니다.'],
    'ja': ['今四半期の売上は、物流部門の新規契約により{pct}%増加しました。',
           'すべての貨物は到着時に検査し、損傷があれば2営業日以内に報告しなければなりません。',
           '委員会は、試験プログラムをさらに3つの地域事務所に拡大することを推奨します。',
           '従業員は、会計年度末までに年次セキュリティ研修を修了する必要があります。',
           '顧客満足度は3四半期連続で上昇し、過去最高を記録しました。',
           '契約の変更はすべて書面で行い、両当事者が署名しなければなりません。',
           '新しい倉庫は来年の春に開設され、約{n}人を雇用する予定です。',
           'エネルギー費用は計画を上回りましたが、輸送費の削減で増加分の大部分を相殺しました。',
           'この書類は保管し、内容に誤りがある場合はサービスデスクまでご連絡ください。',
           '本装置は、温度5度から30度の乾燥した場所に保管してください。',
           '主要生産ラインの保守点検は、毎月第1週末に予定されています。',
           '監査では重大な不備は見つからず、昨年の勧告はすべて実施されました。'],
    'zh': ['本季度收入增长了{pct}%，主要得益于物流业务的新合同。',
           '所有货物到达后必须立即检查，如有损坏须在两个工作日内报告。',
           '委员会建议将试点项目再扩大到三个地区办事处。',
           '员工必须在财政年度结束前完成年度安全培训。',
           '客户满意度连续第三个季度上升，创下历史新高。',
           '对协议的任何修改都必须以书面形式作出，并由双方签字。',
           '新仓库将于明年春季启用，预计雇用约{n}名员工。',
           '能源成本高于计划，但运输方面的节约抵消了大部分增幅。',
           '请保留本文件，如有任何信息有误，请联系服务台。',
           '本设备应存放在干燥处，温度保持在5至30度之间。',
           '主生产线的维护工作定于每月第一个周末进行。',
           '审计未发现重大缺陷，去年的所有建议均已落实。'],
    'ar': ['ارتفعت الإيرادات بنسبة {pct}% هذا الربع، ويعود ذلك أساسًا إلى العقود الجديدة في قطاع الخدمات اللوجستية.',
           'يجب فحص جميع الشحنات عند وصولها، والإبلاغ عن أي تلف خلال يومي عمل.',
           'توصي اللجنة بتوسيع البرنامج التجريبي ليشمل ثلاثة مكاتب إقليمية أخرى.',
           'يجب على الموظفين إكمال التدريب الأمني السنوي قبل نهاية السنة المالية.',
           'ارتفع رضا العملاء للربع الثالث على التوالي وبلغ مستوى قياسيًا.',
           'يجب أن يكون أي تعديل على الاتفاقية مكتوبًا وموقّعًا من الطرفين.',
           'سيُفتتح المستودع الجديد في الربيع المقبل وسيوظف نحو {n} شخص.',
           'كانت تكاليف الطاقة أعلى من المخطط، لكن الوفورات في النقل عوّضت معظم الزيادة.',
           'يرجى الاحتفاظ بهذه الوثيقة والتواصل مع مكتب الخدمة إذا كانت أي بيانات غير صحيحة.',
           'يجب تخزين الجهاز في مكان جاف وفي درجة حرارة تتراوح بين 5 و30 درجة.',
           'من المقرر إجراء صيانة خط الإنتاج الرئيسي في عطلة نهاية الأسبوع الأولى من كل شهر.',
           'لم يكشف التدقيق عن أي نقاط ضعف جوهرية، وتم تنفيذ جميع توصيات العام الماضي.'],
    'hi': ['इस तिमाही में राजस्व {pct}% बढ़ा, जिसका मुख्य कारण लॉजिस्टिक्स क्षेत्र के नए अनुबंध हैं।',
           'सभी शिपमेंट की पहुँचने पर जाँच की जानी चाहिए और किसी भी क्षति की सूचना दो कार्यदिवसों के भीतर दी जानी चाहिए।',
           'समिति पायलट कार्यक्रम को तीन और क्षेत्रीय कार्यालयों तक बढ़ाने की सिफारिश करती है।',
           'कर्मचारियों को वित्तीय वर्ष समाप्त होने से पहले वार्षिक सुरक्षा प्रशिक्षण पूरा करना होगा।',
           'ग्राहक संतुष्टि लगातार तीसरी तिमाही में बढ़ी और रिकॉर्ड स्तर पर पहुँच गई।',
           'समझौते में कोई भी बदलाव लिखित रूप में होना चाहिए और दोनों पक्षों द्वारा हस्ताक्षरित होना चाहिए।',
           'नया गोदाम अगले वसंत में खुलेगा और लगभग {n} लोगों को रोज़गार देगा।',
           'ऊर्जा लागत योजना से अधिक रही, लेकिन परिवहन में बचत ने अधिकांश वृद्धि की भरपाई कर दी।',
           'कृपया इस दस्तावेज़ को सुरक्षित रखें और कोई विवरण गलत होने पर सेवा डेस्क से संपर्क करें।',
           'उपकरण को 5 से 30 डिग्री तापमान वाले सूखे स्थान पर रखा जाना चाहिए।',
           'मुख्य उत्पादन लाइन का रखरखाव हर महीने के पहले सप्ताहांत में निर्धारित है।',
           'ऑडिट में कोई गंभीर कमी नहीं पाई गई और पिछले वर्ष की सभी सिफारिशें लागू कर दी गईं।'],
    'bn': ['এই প্রান্তিকে আয় {pct}% বেড়েছে, মূলত লজিস্টিকস বিভাগের নতুন চুক্তির কারণে।',
           'সব চালান পৌঁছানোর পর পরীক্ষা করতে হবে এবং কোনো ক্ষতি হলে দুই কর্মদিবসের মধ্যে জানাতে হবে।',
           'কমিটি পাইলট কর্মসূচিটি আরও তিনটি আঞ্চলিক অফিসে সম্প্রসারণের সুপারিশ করছে।',
           'কর্মীদের অর্থবছর শেষ হওয়ার আগে বার্ষিক নিরাপত্তা প্রশিক্ষণ সম্পন্ন করতে হবে।',
           'গ্রাহক সন্তুষ্টি টানা তৃতীয় প্রান্তিকে বেড়ে রেকর্ড উচ্চতায় পৌঁছেছে।',
           'চুক্তির যেকোনো পরিবর্তন লিখিতভাবে করতে হবে এবং উভয় পক্ষকে স্বাক্ষর করতে হবে।',
           'নতুন গুদামটি আগামী বসন্তে চালু হবে এবং প্রায় {n} জনকে নিয়োগ দেবে।',
           'জ্বালানি খরচ পরিকল্পনার চেয়ে বেশি ছিল, তবে পরিবহনে সাশ্রয় বৃদ্ধির বেশিরভাগ পূরণ করেছে।',
           'অনুগ্রহ করে এই নথিটি সংরক্ষণ করুন এবং কোনো তথ্য ভুল হলে সেবা ডেস্কে যোগাযোগ করুন।',
           'যন্ত্রটি 5 থেকে 30 ডিগ্রি তাপমাত্রার একটি শুকনো স্থানে রাখতে হবে।',
           'প্রধান উৎপাদন লাইনের রক্ষণাবেক্ষণ প্রতি মাসের প্রথম সপ্তাহান্তে নির্ধারিত।',
           'নিরীক্ষায় কোনো গুরুতর দুর্বলতা পাওয়া যায়নি এবং গত বছরের সব সুপারিশ বাস্তবায়িত হয়েছে।'],
    'fr': ["Le chiffre d'affaires a progressé de {pct} % ce trimestre, principalement grâce aux nouveaux contrats du secteur logistique.",
           'Toutes les livraisons doivent être contrôlées à leur arrivée et tout dommage signalé dans un délai de deux jours ouvrés.',
           "Le comité recommande d'étendre le programme pilote à trois autres bureaux régionaux.",
           "Les employés doivent suivre la formation annuelle à la sécurité avant la fin de l'exercice financier.",
           'La satisfaction des clients a augmenté pour le troisième trimestre consécutif et atteint un niveau record.',
           'Toute modification du contrat doit être faite par écrit et signée par les deux parties.',
           'Le nouvel entrepôt ouvrira au printemps prochain et emploiera environ {n} personnes.',
           "Les coûts de l'énergie ont dépassé les prévisions, mais les économies sur le transport ont compensé l'essentiel de la hausse.",
           "Veuillez conserver ce document et contacter le service d'assistance si des informations sont inexactes.",
           "L'appareil doit être stocké dans un endroit sec, à une température comprise entre 5 et 30 degrés.",
           'La maintenance de la ligne de production principale est prévue le premier week-end de chaque mois.',
           "L'audit n'a relevé aucune faiblesse significative et toutes les recommandations de l'an dernier ont été mises en œuvre."],
    'de': ['Der Umsatz stieg in diesem Quartal um {pct} Prozent, vor allem dank neuer Verträge im Logistikbereich.',
           'Alle Lieferungen müssen bei Ankunft geprüft und Schäden innerhalb von zwei Werktagen gemeldet werden.',
           'Der Ausschuss empfiehlt, das Pilotprogramm auf drei weitere Regionalbüros auszuweiten.',
           'Die Mitarbeitenden müssen die jährliche Sicherheitsschulung vor Ende des Geschäftsjahres abschließen.',
           'Die Kundenzufriedenheit stieg das dritte Quartal in Folge und erreichte einen Rekordwert.',
           'Jede Änderung des Vertrags muss schriftlich erfolgen und von beiden Parteien unterzeichnet werden.',
           'Das neue Lager wird im nächsten Frühjahr eröffnet und rund {n} Personen beschäftigen.',
           'Die Energiekosten lagen über dem Plan, doch Einsparungen beim Transport glichen den Großteil des Anstiegs aus.',
           'Bitte bewahren Sie dieses Dokument auf und wenden Sie sich an den Service-Desk, falls Angaben nicht stimmen.',
           'Das Gerät ist an einem trockenen Ort bei einer Temperatur zwischen 5 und 30 Grad zu lagern.',
           'Die Wartung der Hauptproduktionslinie ist jeweils für das erste Wochenende des Monats geplant.',
           'Die Prüfung ergab keine wesentlichen Schwächen, und alle Empfehlungen des Vorjahres wurden umgesetzt.'],
    'es': ['Los ingresos aumentaron un {pct} % este trimestre, principalmente gracias a nuevos contratos en el sector logístico.',
           'Todos los envíos deben inspeccionarse a su llegada y cualquier daño debe notificarse en un plazo de dos días hábiles.',
           'El comité recomienda ampliar el programa piloto a otras tres oficinas regionales.',
           'Los empleados deben completar la formación anual en seguridad antes del cierre del ejercicio fiscal.',
           'La satisfacción de los clientes aumentó por tercer trimestre consecutivo y alcanzó un máximo histórico.',
           'Cualquier modificación del contrato debe hacerse por escrito y estar firmada por ambas partes.',
           'El nuevo almacén abrirá la próxima primavera y empleará a unas {n} personas.',
           'Los costes energéticos superaron lo previsto, pero el ahorro en transporte compensó la mayor parte del aumento.',
           'Conserve este documento y póngase en contacto con el servicio de asistencia si algún dato es incorrecto.',
           'El dispositivo debe guardarse en un lugar seco, a una temperatura de entre 5 y 30 grados.',
           'El mantenimiento de la línea de producción principal está previsto para el primer fin de semana de cada mes.',
           'La auditoría no encontró deficiencias significativas y se aplicaron todas las recomendaciones del año pasado.'],
    'pt': ['A receita cresceu {pct}% neste trimestre, principalmente graças a novos contratos no setor de logística.',
           'Todas as remessas devem ser inspecionadas na chegada e qualquer dano deve ser comunicado em até dois dias úteis.',
           'O comitê recomenda ampliar o programa piloto para mais três escritórios regionais.',
           'Os funcionários devem concluir o treinamento anual de segurança antes do fim do exercício fiscal.',
           'A satisfação dos clientes aumentou pelo terceiro trimestre consecutivo e atingiu um recorde.',
           'Qualquer alteração do contrato deve ser feita por escrito e assinada por ambas as partes.',
           'O novo armazém será inaugurado na próxima primavera e empregará cerca de {n} pessoas.',
           'Os custos de energia ficaram acima do previsto, mas a economia no transporte compensou a maior parte do aumento.',
           'Guarde este documento e entre em contato com a central de atendimento se algum dado estiver incorreto.',
           'O aparelho deve ser armazenado em local seco, a uma temperatura entre 5 e 30 graus.',
           'A manutenção da linha de produção principal está programada para o primeiro fim de semana de cada mês.',
           'A auditoria não encontrou falhas relevantes e todas as recomendações do ano passado foram implementadas.'],
}
# Key English words each statement must keep in a translation into English (meaning check).
KEYS = [(r'revenue|sales|income|turnover', r'contract'), (r'shipment|cargo|consignment|goods|deliver|freight', r'damage'),
        (r'committee', r'office'), (r'training', r'fiscal|financial|business year'), (r'satisf', r'record|high'),
        (r'writ', r'sign'), (r'warehouse|storehouse|depot', r'open|launch|start|operat|commission|in service'), (r'energy|power', r'transport'),
        (r'document|record|paper', r'desk|support|service|help'), (r'dry', r'temperature|degree'),
        (r'mainten|servic|inspect', r'weekend'), (r'audit|review|inspection', r'recommend')]

HEADINGS = {
    'en': ['Summary', 'Key results', 'Financial overview', 'Operations', 'Risks', 'Next steps', 'Product details', 'Terms and conditions'],
    'ko': ['요약', '주요 결과', '재무 현황', '운영 현황', '위험 요소', '다음 단계', '제품 정보', '계약 조건'],
    'ja': ['概要', '主な結果', '財務状況', '業務状況', 'リスク', '今後の予定', '製品情報', '取引条件'],
    'zh': ['摘要', '主要结果', '财务概况', '运营情况', '风险', '后续步骤', '产品信息', '条款与条件'],
    'ar': ['ملخص', 'النتائج الرئيسية', 'الوضع المالي', 'العمليات', 'المخاطر', 'الخطوات التالية', 'تفاصيل المنتج', 'الشروط والأحكام'],
    'hi': ['सारांश', 'मुख्य परिणाम', 'वित्तीय स्थिति', 'संचालन', 'जोखिम', 'अगले कदम', 'उत्पाद विवरण', 'नियम और शर्तें'],
    'bn': ['সারসংক্ষেপ', 'প্রধান ফলাফল', 'আর্থিক অবস্থা', 'কার্যক্রম', 'ঝুঁকি', 'পরবর্তী পদক্ষেপ', 'পণ্যের বিবরণ', 'শর্তাবলি'],
    'fr': ['Résumé', 'Principaux résultats', 'Situation financière', 'Activités', 'Risques', 'Prochaines étapes', 'Détails du produit', 'Conditions générales'],
    'de': ['Zusammenfassung', 'Wichtigste Ergebnisse', 'Finanzlage', 'Betrieb', 'Risiken', 'Nächste Schritte', 'Produktdetails', 'Allgemeine Bedingungen'],
    'es': ['Resumen', 'Resultados principales', 'Situación financiera', 'Operaciones', 'Riesgos', 'Próximos pasos', 'Detalles del producto', 'Términos y condiciones'],
    'pt': ['Resumo', 'Principais resultados', 'Situação financeira', 'Operações', 'Riscos', 'Próximos passos', 'Detalhes do produto', 'Termos e condições'],
}
TABLE = {  # item, code, quantity, unit price, total, date
    'en': ['Item', 'Code', 'Quantity', 'Unit price', 'Total', 'Date'], 'ko': ['품목', '코드', '수량', '단가', '합계', '날짜'],
    'ja': ['品目', 'コード', '数量', '単価', '合計', '日付'], 'zh': ['项目', '代码', '数量', '单价', '合计', '日期'],
    'ar': ['البند', 'الرمز', 'الكمية', 'سعر الوحدة', 'الإجمالي', 'التاريخ'], 'hi': ['वस्तु', 'कोड', 'मात्रा', 'इकाई मूल्य', 'कुल', 'तारीख'],
    'bn': ['পণ্য', 'কোড', 'পরিমাণ', 'একক মূল্য', 'মোট', 'তারিখ'], 'fr': ['Article', 'Code', 'Quantité', 'Prix unitaire', 'Total', 'Date'],
    'de': ['Artikel', 'Code', 'Menge', 'Stückpreis', 'Summe', 'Datum'], 'es': ['Artículo', 'Código', 'Cantidad', 'Precio unitario', 'Total', 'Fecha'],
    'pt': ['Item', 'Código', 'Quantidade', 'Preço unitário', 'Total', 'Data'],
}
ITEMS = {
    'en': ['Barcode scanner', 'Safety gloves', 'Thermal labels', 'Network switch', 'Office chair', 'Consulting hours'],
    'ko': ['바코드 스캐너', '안전 장갑', '감열 라벨', '네트워크 스위치', '사무용 의자', '컨설팅 시간'],
    'ja': ['バーコードスキャナー', '安全手袋', '感熱ラベル', 'ネットワークスイッチ', '事務用椅子', 'コンサルティング時間'],
    'zh': ['条码扫描器', '安全手套', '热敏标签', '网络交换机', '办公椅', '咨询工时'],
    'ar': ['ماسح الباركود', 'قفازات السلامة', 'ملصقات حرارية', 'محوّل شبكة', 'كرسي مكتب', 'ساعات استشارة'],
    'hi': ['बारकोड स्कैनर', 'सुरक्षा दस्ताने', 'थर्मल लेबल', 'नेटवर्क स्विच', 'कार्यालय कुर्सी', 'परामर्श घंटे'],
    'bn': ['বারকোড স্ক্যানার', 'নিরাপত্তা দস্তানা', 'থার্মাল লেবেল', 'নেটওয়ার্ক সুইচ', 'অফিস চেয়ার', 'পরামর্শ ঘণ্টা'],
    'fr': ['Lecteur de codes-barres', 'Gants de sécurité', 'Étiquettes thermiques', 'Commutateur réseau', 'Chaise de bureau', 'Heures de conseil'],
    'de': ['Barcodescanner', 'Schutzhandschuhe', 'Thermoetiketten', 'Netzwerk-Switch', 'Bürostuhl', 'Beratungsstunden'],
    'es': ['Escáner de códigos de barras', 'Guantes de seguridad', 'Etiquetas térmicas', 'Conmutador de red', 'Silla de oficina', 'Horas de consultoría'],
    'pt': ['Leitor de código de barras', 'Luvas de segurança', 'Etiquetas térmicas', 'Switch de rede', 'Cadeira de escritório', 'Horas de consultoria'],
}
FORM = {  # full name, employee ID, email, phone, start date, department, office, manager
    'en': ['Full name', 'Employee ID', 'Email', 'Phone', 'Start date', 'Department', 'Office', 'Manager'],
    'ko': ['성명', '사번', '이메일', '전화번호', '입사일', '부서', '근무지', '관리자'],
    'ja': ['氏名', '社員番号', 'メールアドレス', '電話番号', '入社日', '部署', '勤務地', '上長'],
    'zh': ['姓名', '员工编号', '电子邮件', '电话', '入职日期', '部门', '办公地点', '主管'],
    'ar': ['الاسم الكامل', 'رقم الموظف', 'البريد الإلكتروني', 'الهاتف', 'تاريخ البدء', 'القسم', 'المكتب', 'المدير'],
    'hi': ['पूरा नाम', 'कर्मचारी आईडी', 'ईमेल', 'फ़ोन', 'कार्यग्रहण तिथि', 'विभाग', 'कार्यालय', 'प्रबंधक'],
    'bn': ['পূর্ণ নাম', 'কর্মী আইডি', 'ইমেইল', 'ফোন', 'যোগদানের তারিখ', 'বিভাগ', 'অফিস', 'ব্যবস্থাপক'],
    'fr': ['Nom complet', 'Matricule', 'E-mail', 'Téléphone', "Date d'entrée", 'Service', 'Bureau', 'Responsable'],
    'de': ['Vollständiger Name', 'Personalnummer', 'E-Mail', 'Telefon', 'Eintrittsdatum', 'Abteilung', 'Standort', 'Vorgesetzte Person'],
    'es': ['Nombre completo', 'ID de empleado', 'Correo electrónico', 'Teléfono', 'Fecha de incorporación', 'Departamento', 'Oficina', 'Responsable'],
    'pt': ['Nome completo', 'ID do funcionário', 'E-mail', 'Telefone', 'Data de admissão', 'Departamento', 'Escritório', 'Gestor'],
}
LABELS_KEYS = ['report', 'invoice', 'manual', 'news', 'warning', 'warn_text', 'figure', 'subtotal', 'total', 'bill_to', 'terms', 'prepared', 'issue']
LABELS = {
    'en': ['Quarterly Report', 'Invoice', 'Installation Guide', 'Company News', 'Warning', 'Disconnect the power supply before opening the cover.',
           'Figure', 'Subtotal', 'Total due', 'Bill to', 'Payment terms: 30 days', 'Prepared by', 'Issue'],
    'ko': ['분기 보고서', '청구서', '설치 안내서', '사내 소식', '경고', '덮개를 열기 전에 반드시 전원을 차단하십시오.',
           '그림', '소계', '총 청구액', '청구 대상', '결제 조건: 30일 이내', '작성자', '호'],
    'ja': ['四半期報告書', '請求書', '設置ガイド', '社内ニュース', '警告', 'カバーを開ける前に必ず電源を切ってください。',
           '図', '小計', 'ご請求金額', '請求先', '支払条件：30日以内', '作成者', '号'],
    'zh': ['季度报告', '发票', '安装指南', '公司新闻', '警告', '打开外壳前请务必切断电源。',
           '图', '小计', '应付总额', '收票方', '付款条件：30天内', '编制人', '期'],
    'ar': ['التقرير الفصلي', 'فاتورة', 'دليل التركيب', 'أخبار الشركة', 'تحذير', 'افصل مصدر الطاقة قبل فتح الغطاء.',
           'الشكل', 'المجموع الفرعي', 'المبلغ المستحق', 'الفاتورة إلى', 'شروط الدفع: 30 يومًا', 'إعداد', 'العدد'],
    'hi': ['तिमाही रिपोर्ट', 'चालान', 'स्थापना गाइड', 'कंपनी समाचार', 'चेतावनी', 'कवर खोलने से पहले बिजली की आपूर्ति बंद करें।',
           'चित्र', 'उप-योग', 'कुल देय राशि', 'बिल प्राप्तकर्ता', 'भुगतान की शर्तें: 30 दिन', 'तैयारकर्ता', 'अंक'],
    'bn': ['ত্রৈমাসিক প্রতিবেদন', 'চালান', 'স্থাপন নির্দেশিকা', 'প্রতিষ্ঠানের খবর', 'সতর্কতা', 'ঢাকনা খোলার আগে বিদ্যুৎ সংযোগ বিচ্ছিন্ন করুন।',
           'চিত্র', 'উপমোট', 'মোট প্রদেয়', 'বিল প্রাপক', 'পরিশোধের শর্ত: 30 দিন', 'প্রস্তুতকারী', 'সংখ্যা'],
    'fr': ['Rapport trimestriel', 'Facture', "Guide d'installation", "Actualités de l'entreprise", 'Avertissement',
           "Débranchez l'alimentation avant d'ouvrir le capot.", 'Figure', 'Sous-total', 'Total à payer', 'Facturé à',
           'Conditions de paiement : 30 jours', 'Préparé par', 'Numéro'],
    'de': ['Quartalsbericht', 'Rechnung', 'Installationsanleitung', 'Firmennachrichten', 'Warnung',
           'Trennen Sie die Stromversorgung, bevor Sie die Abdeckung öffnen.', 'Abbildung', 'Zwischensumme', 'Gesamtbetrag',
           'Rechnungsempfänger', 'Zahlungsbedingungen: 30 Tage', 'Erstellt von', 'Ausgabe'],
    'es': ['Informe trimestral', 'Factura', 'Guía de instalación', 'Noticias de la empresa', 'Advertencia',
           'Desconecte la alimentación antes de abrir la tapa.', 'Figura', 'Subtotal', 'Total a pagar', 'Facturar a',
           'Condiciones de pago: 30 días', 'Preparado por', 'Número'],
    'pt': ['Relatório trimestral', 'Fatura', 'Guia de instalação', 'Notícias da empresa', 'Aviso',
           'Desligue a alimentação antes de abrir a tampa.', 'Figura', 'Subtotal', 'Total a pagar', 'Faturar para',
           'Condições de pagamento: 30 dias', 'Preparado por', 'Edição'],
}
PEOPLE = {
    'en': ['Olivia Grant', 'Daniel Okafor', 'Emily Roberts', 'Thomas Becker'], 'ko': ['김민준', '이서연', '박지훈', '최유나'],
    'ja': ['佐藤健太', '鈴木美咲', '高橋大輔', '田中愛'], 'zh': ['王伟', '李娜', '张强', '刘洋'],
    'ar': ['أحمد منصور', 'ليلى حداد', 'يوسف الخطيب', 'سارة العلي'], 'hi': ['अनिल शर्मा', 'प्रिया वर्मा', 'राहुल गुप्ता', 'नेहा सिंह'],
    'bn': ['রহিম উদ্দিন', 'নুসরাত জাহান', 'তানভীর হাসান', 'ফারহানা ইসলাম'], 'fr': ['Claire Dubois', 'Julien Martin', 'Sophie Laurent', 'Hugo Petit'],
    'de': ['Anna Schmidt', 'Lukas Weber', 'Mia Fischer', 'Jonas Wagner'], 'es': ['Lucía García', 'Javier López', 'Carmen Ruiz', 'Diego Torres'],
    'pt': ['Ana Souza', 'Pedro Almeida', 'Beatriz Costa', 'Rafael Lima'],
}
COMPANIES = ['Northwind Logistics', 'Halcyon Biotech', 'Bluepeak Energy', 'Orchid Retail Group', 'Copperleaf Software', 'Atlas Engineering']
FONTS = {  # Windows fonts the browser uses; the picture renderer uses the files
    'en': ('Segoe UI', 'segoeui.ttf'), 'fr': ('Segoe UI', 'segoeui.ttf'), 'de': ('Segoe UI', 'segoeui.ttf'),
    'es': ('Segoe UI', 'segoeui.ttf'), 'pt': ('Segoe UI', 'segoeui.ttf'), 'ko': ('Malgun Gothic', 'malgun.ttf'),
    'ja': ('Yu Gothic', 'YuGothM.ttc'), 'zh': ('Microsoft YaHei', 'msyh.ttc'), 'ar': ('Segoe UI', 'segoeui.ttf'),
    'hi': ('Nirmala UI', 'Nirmala.ttc'), 'bn': ('Nirmala UI', 'Nirmala.ttc'),
}
PAGES = {'report': 2, 'invoice': 1, 'manual': 2, 'news': 2}
PALETTES = [('#1f3a5f', '#e8eef6'), ('#5b2a86', '#f1eaf7'), ('#0f5132', '#e7f4ec'), ('#7a2e0e', '#fbefe8'), ('#333333', '#f2f2f2')]


def esc(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


class Values:
    """Random literal values (the checker expects each one back unchanged)."""

    def __init__(self, rng):
        self.rng = rng

    def code(self, prefix=None):
        prefix = prefix or ''.join(self.rng.choice(string.ascii_uppercase) for _ in range(3))
        return f'{prefix}-{self.rng.randint(2019, 2027)}-{self.rng.randint(1, 99999):05d}'

    def email(self):
        return f'{self.rng.choice(["ops", "billing", "service", "hr", "info"])}{self.rng.randint(1, 99)}@{self.rng.choice(["example.com", "corp.example"])}'

    def phone(self):
        r = self.rng
        return r.choice([f'+82 10 {r.randint(1000, 9999)} {r.randint(1000, 9999)}', f'+1 415 555 {r.randint(1000, 9999)}',
                         f'+44 20 7946 {r.randint(100, 999)}', f'+49 30 {r.randint(100000, 999999)}', f'+81 3 {r.randint(1000, 9999)} {r.randint(1000, 9999)}'])

    def amount(self):
        currency = self.rng.choice(['USD', 'EUR', 'KRW', 'JPY', 'GBP'])
        value = self.rng.randint(120, 950000) * (1000 if currency in ('KRW', 'JPY') else 1)
        return f'{currency} {value:,}' + ('' if currency in ('KRW', 'JPY') else f'.{self.rng.randint(0, 99):02d}')

    def date(self):
        return f'{self.rng.randint(2024, 2027)}-{self.rng.randint(1, 12):02d}-{self.rng.randint(1, 28):02d}'


class Writer:
    """HTML for one document; records which statements it used (for the meaning check)."""

    def __init__(self, rng, lang):
        self.rng, self.lang, self.values, self.used = rng, lang, Values(rng), set()

    def sentence(self, index=None):
        index = self.rng.randrange(12) if index is None else index
        self.used.add(index)
        return SENTENCES[self.lang][index].format(pct=self.rng.randint(3, 19), n=self.rng.randint(40, 900))

    def paragraph(self, k=None):
        return '<p>' + ' '.join(esc(self.sentence()) for _ in range(k or self.rng.randint(2, 4))) + '</p>'

    def heading(self, level=2):
        return f'<h{level}>{esc(self.rng.choice(HEADINGS[self.lang]))}</h{level}>'

    def table(self, rows):
        head = ''.join(f'<th>{esc(h)}</th>' for h in TABLE[self.lang])
        body = ''.join('<tr>' + ''.join(f'<td>{esc(c)}</td>' for c in (
            self.rng.choice(ITEMS[self.lang]), self.values.code(), str(self.rng.randint(1, 500)),
            self.values.amount(), self.values.amount(), self.values.date())) + '</tr>' for _ in range(rows))
        return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    def warning(self):
        label = dict(zip(LABELS_KEYS, LABELS[self.lang]))
        return f'<div class="warn"><b>{esc(label["warning"])}:</b> {esc(label["warn_text"])} {esc(self.sentence(9))}</div>'

    def form(self):
        person = self.rng.choice(PEOPLE[self.lang])
        values = [person, self.values.code('EMP'), self.values.email(), self.values.phone(), self.values.date(),
                  self.rng.choice(HEADINGS[self.lang][3:5]), self.rng.choice(COMPANIES), self.rng.choice(PEOPLE[self.lang])]
        return '<table class="form">' + ''.join(f'<tr><td class="label">{esc(a)}</td><td>{esc(b)}</td></tr>'
                                                for a, b in zip(FORM[self.lang], values)) + '</table>'


def body(kind, rng, lang, picture):
    w = Writer(rng, lang)
    label = dict(zip(LABELS_KEYS, LABELS[lang]))
    company, person = rng.choice(COMPANIES), rng.choice(PEOPLE[lang])
    meta = f'<p class="meta">{esc(label["prepared"])}: {esc(person)} · {w.values.date()} · {w.values.code()}</p>'
    if kind == 'report':
        html = f'<div class="band"><h1>{esc(company)} — {esc(label["report"])}</h1></div>' + meta
        html += w.heading() + w.paragraph(4) + '<ul>' + ''.join(f'<li>{esc(w.sentence())}</li>' for _ in range(4)) + '</ul>'
        html += w.heading() + w.table(rng.randint(6, 9)) + w.paragraph(3) + w.warning()
        html += '<div class="cols">' + ''.join(w.heading(3) + w.paragraph(3) for _ in range(6)) + '</div>'
        html += ''.join(w.heading() + w.paragraph(4) + w.table(5) for _ in range(3))
    elif kind == 'invoice':
        html = (f'<div class="band"><h1>{esc(label["invoice"])} {w.values.code("INV")}</h1></div>'
                f'<div class="grid"><div><b>{esc(label["bill_to"])}</b><br/>{esc(rng.choice(COMPANIES))}<br/>{esc(person)}<br/>'
                f'{w.values.email()}<br/>{w.values.phone()}</div><div>{w.form()}</div></div>')
        html += w.table(12) + (f'<p class="right"><b>{esc(label["subtotal"])}:</b> {w.values.amount()}<br/>'
                               f'<b>{esc(label["total"])}:</b> {w.values.amount()}</p>')
        html += f'<h3>{esc(label["terms"])}</h3>' + w.paragraph(3) + w.paragraph(3) + w.warning() + w.paragraph(4) * 3
    elif kind == 'manual':
        html = f'<div class="band"><h1>{esc(label["manual"])} — {w.values.code("MX")}</h1></div>' + meta
        html += w.heading() + w.paragraph(3) + '<ol>' + ''.join(f'<li>{esc(w.sentence())}</li>' for _ in range(6)) + '</ol>'
        html += w.warning() + (f'<figure><img src="{picture}"/><figcaption>{esc(label["figure"])} 1 · {esc(w.sentence(9))}</figcaption></figure>')
        html += w.table(5) + w.heading() + w.paragraph(4) + w.warning()
        html += ''.join(w.heading() + w.paragraph(3) + '<ol>' + ''.join(f'<li>{esc(w.sentence())}</li>' for _ in range(4)) + '</ol>' for _ in range(9))
    elif kind == 'news':
        html = (f'<div class="band"><h1>{esc(company)} · {esc(label["news"])}</h1></div>'
                f'<p class="meta">{esc(label["issue"])} {rng.randint(1, 80)} · {w.values.date()}</p><div class="cols">')
        for _ in range(24):
            html += w.heading(3) + w.paragraph(3)
            if rng.random() < 0.3:
                html += f'<blockquote>{esc(w.sentence())}</blockquote>'
            if rng.random() < 0.2:
                html += w.table(4)
        html += '</div>'
    else:
        raise ValueError(kind)
    return html, w.used


def page_css(lang, palette):
    dark, light = palette
    family = FONTS[lang][0]
    direction = 'rtl' if lang == 'ar' else 'ltr'
    return f"""
@page {{ size: A4; margin: 16mm 15mm 16mm 15mm; }}
html {{ direction: {direction}; }}
body {{ font-family: '{family}', 'Segoe UI', sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1d2430; margin: 0; }}
.band {{ background: {dark}; color: #fff; padding: 3mm 4mm; margin-bottom: 3mm; }}
.band h1 {{ color: #fff; margin: 0; font-size: 17pt; }}
h1 {{ font-size: 17pt; }} h2 {{ font-size: 13pt; color: {dark}; margin: 4mm 0 1.5mm; }} h3 {{ font-size: 11pt; color: {dark}; margin: 3mm 0 1mm; }}
p {{ margin: 0 0 2mm; text-align: justify; }} .meta {{ color: #5a6676; font-size: 9pt; }} .right {{ text-align: end; }}
table {{ width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; font-size: 9pt; }}
th {{ background: {light}; text-align: start; padding: 1.3mm 2mm; border: 0.3mm solid #b9c8d6; }}
td {{ padding: 1.2mm 2mm; border: 0.3mm solid #c9d4de; }}
table.form td.label {{ background: {light}; font-weight: bold; width: 40%; }}
.grid {{ display: grid; grid-template-columns: 1fr 1.4fr; gap: 6mm; margin-bottom: 3mm; }}
.cols {{ column-count: 2; column-gap: 7mm; }}
.warn {{ border: 0.4mm solid #d08a1c; background: #fff6e8; padding: 2.5mm 3.5mm; margin: 2mm 0 3mm; break-inside: avoid; }}
blockquote {{ border-inline-start: 1.2mm solid {dark}; margin: 2mm 0; padding: 1mm 3mm; font-style: italic; color: #33404f; }}
figure {{ margin: 2mm 0 3mm; break-inside: avoid; }} figure img {{ width: 75mm; }} figcaption {{ font-size: 8.5pt; color: #5a6676; }}
ol, ul {{ margin: 0 0 2.5mm; padding-inline-start: 6mm; }} li {{ margin-bottom: 1mm; }}
"""


def picture(rng, lang, folder):
    """A PNG label with text in its pixels (the engine reads it with OCR)."""
    label = dict(zip(LABELS_KEYS, LABELS[lang]))
    values = Values(rng)
    font = Path('C:/Windows/Fonts') / FONTS[lang][1]
    direction = 'rtl' if lang == 'ar' else 'ltr'
    html = (f'<div style="direction:{direction}"><p style="font-size:22px;font-weight:bold;margin:0">{esc(label["warning"])}</p>'
            f'<p style="font-size:15px;margin:4px 0">{esc(label["warn_text"])}</p>'
            f'<p style="font-size:18px;color:#8b1a1a;margin:4px 0">{values.code()}</p></div>')
    doc = pymupdf.open()
    page = doc.new_page(width=360, height=150)
    page.draw_rect(page.rect, color=(0.1, 0.1, 0.1), fill=(1, 0.88, 0.25), width=4)
    archive = pymupdf.Archive(str(font.parent))
    css = f'@font-face {{font-family: f; src: url({font.name});}} * {{font-family: f;}}'
    page.insert_htmlbox(pymupdf.Rect(14, 12, 346, 140), html, css=css, archive=archive)
    path = folder / f'picture-{lang}-{rng.randint(0, 10**6)}.png'
    page.get_pixmap(dpi=150).save(str(path))
    return path


def print_pdf(edge, html_path, pdf_path):
    for _ in range(4):
        pdf_path.unlink(missing_ok=True)
        subprocess.run([edge, '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--allow-file-access-from-files',
                        f'--print-to-pdf={pdf_path}', html_path.as_uri()], timeout=180, capture_output=True)
        for _ in range(30):
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return
            time.sleep(0.5)
    raise RuntimeError(f'the browser did not print {html_path}')


def fill(page):
    """How far down the page its text reaches, as a share of the printable height."""
    bottoms = [b[3] for b in page.get_text('blocks') if b[4].strip()]
    return (max(bottoms) / page.rect.height) if bottoms else 0.0


def cut_to_full_pages(pdf_path, pages):
    with pymupdf.open(pdf_path) as doc:
        keep = min(pages, len(doc))
        if keep == len(doc) and fill(doc[-1]) < 0.8:
            keep -= 1  # the last page is only partly filled
        if keep < 1:
            raise RuntimeError(f'{pdf_path.name}: less than one full page of content')
        out = pymupdf.open()
        out.insert_pdf(doc, to_page=keep - 1)
    out.save(pdf_path.with_suffix('.cut.pdf'), garbage=3, deflate=True)
    out.close()
    pdf_path.with_suffix('.cut.pdf').replace(pdf_path)
    return keep


def scanned(src_path, out_path, rng):
    from evaluation.robustness.make_corpus import scanned_copy
    with pymupdf.open(src_path) as src:
        scan = scanned_copy(src, rng, dpi=rng.choice([150, 200]))
        scan.save(out_path, garbage=3, deflate=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('out')
    parser.add_argument('--set', choices=['dev', 'heldout', 'competition'], default='dev')
    parser.add_argument('--langs', default=','.join(LANGS))
    parser.add_argument('--edge', default=r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
    args = parser.parse_args()
    # Absolute: the browser resolves a relative --print-to-pdf path against its own folder.
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    seed = {'dev': 20261121, 'heldout': 99117, 'competition': 20261204}[args.set]
    manifest = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for li, lang in enumerate(args.langs.split(',')):
            for ki, kind in enumerate(PAGES):
                rng = random.Random(seed * 100 + li * 10 + ki)
                image = picture(rng, lang, tmp) if kind == 'manual' else None
                html, used = body(kind, rng, lang, image.name if image else '')
                html_path = tmp / f'{lang}-{kind}.html'
                html_path.write_text(f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><style>'
                                     f'{page_css(lang, rng.choice(PALETTES))}</style></head><body>{html}</body></html>',
                                     encoding='utf-8')
                name = f'{args.set}-{lang}-{kind}.pdf'
                pdf_path = out / name
                print_pdf(args.edge, html_path, pdf_path)
                pages = cut_to_full_pages(pdf_path, PAGES[kind])
                manifest.append({'file': name, 'lang': lang, 'kind': kind, 'pages': pages, 'statements': sorted(used)})
                if kind == 'invoice':
                    scan_name = f'{args.set}-{lang}-scan.pdf'
                    scanned(pdf_path, out / scan_name, random.Random(seed + li))
                    manifest.append({'file': scan_name, 'lang': lang, 'kind': 'scan', 'pages': pages, 'statements': sorted(used)})
                print(name, pages, 'pages', flush=True)
    (out / 'manifest.json').write_text(json.dumps({'set': args.set, 'documents': manifest}, ensure_ascii=False, indent=1),
                                       encoding='utf-8')
    print(len(manifest), 'documents,', sum(d['pages'] for d in manifest), 'pages')


if __name__ == '__main__':
    main()
