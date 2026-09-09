-- ============================================================
-- 校捷通 · Agent 工具参数 Schema 升级（B5 Function Call）
-- 将种子中的平面占位参数升级为正规 JSON Schema，
-- 提升 LLM Function Call 解析准确率。
-- 执行：本文件后重启后端即可（agent_tool 表运行期读取）。
-- ============================================================
USE xiaojietong;

UPDATE agent_tool SET params_schema = JSON_OBJECT(
  'type','object',
  'properties', JSON_OBJECT(
    'seat_id',    JSON_OBJECT('type','integer','description','座位 ID（如 1=A01、2=A02）'),
    'date',       JSON_OBJECT('type','string','description','预约日期，格式 YYYY-MM-DD'),
    'begin_time', JSON_OBJECT('type','string','description','开始时间，格式 HH:MM，如 15:00'),
    'end_time',   JSON_OBJECT('type','string','description','结束时间，格式 HH:MM，如 17:00')
  ),
  'required', JSON_ARRAY('seat_id','date','begin_time','end_time')
) WHERE name = 'reserve_seat';

UPDATE agent_tool SET params_schema = JSON_OBJECT(
  'type','object',
  'properties', JSON_OBJECT(
    'content',   JSON_OBJECT('type','string','description','提醒内容，如"交作业"'),
    'remind_at', JSON_OBJECT('type','string','description','提醒时间，绝对时间格式 YYYY-MM-DD HH:MM（相对时间先换算，如明天下午4点=次日 16:00）')
  ),
  'required', JSON_ARRAY('content','remind_at')
) WHERE name = 'add_reminder';

UPDATE agent_tool SET params_schema = JSON_OBJECT(
  'type','object',
  'properties', JSON_OBJECT(
    'campus', JSON_OBJECT('type','string','description','校区，可空，如 前卫南区'),
    'floor',  JSON_OBJECT('type','string','description','楼层，可空，如 2')
  ),
  'required', JSON_ARRAY()
) WHERE name = 'query_free_room';

UPDATE agent_tool SET params_schema = JSON_OBJECT(
  'type','object',
  'properties', JSON_OBJECT(
    'title',       JSON_OBJECT('type','string','description','物品标题'),
    'price',       JSON_OBJECT('type','number','description','价格，元'),
    'description', JSON_OBJECT('type','string','description','物品描述，可空'),
    'category',    JSON_OBJECT('type','string','description','分类，可空，如 教材')
  ),
  'required', JSON_ARRAY('title')
) WHERE name = 'post_secondhand';

SELECT name, JSON_PRETTY(params_schema) AS `schema` FROM agent_tool ORDER BY id;
