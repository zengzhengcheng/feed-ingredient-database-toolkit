-- candidate 是待确认别名，不代表数据无效。
SELECT category,
       context,
       raw_term,
       canonical_term,
       status,
       note
FROM dictionaries
WHERE status = 'candidate'
ORDER BY category, context, raw_term;
