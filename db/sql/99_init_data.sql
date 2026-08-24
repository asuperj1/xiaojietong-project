-- ============================================================
-- 校捷通 · 种子数据（最小演示数据集）
-- 执行时机：建表完成后。用于联调与演示。
-- ============================================================
USE xiaojietong;

-- 测试用户（学号/密码为空，微信登录用）
INSERT INTO `user` (`openid`, `nickname`, `student_no`, `major`, `grade`, `campus`, `role`) VALUES
    ('oXJT_TEST_0001', '测试用户A', '20240001', '计算机科学与技术', '2024级', '前卫南区', 0),
    ('oXJT_TEST_0002', '测试用户B', '20240002', '软件工程',           '2024级', '前卫南区', 0)
ON DUPLICATE KEY UPDATE `nickname` = VALUES(`nickname`);

-- 用户兴趣标签
INSERT INTO `user_tag` (`user_id`, `tag`) VALUES
    (1, '学习'), (1, '求职'), (2, '二手'), (2, '生活')
ON DUPLICATE KEY UPDATE `tag` = VALUES(`tag`);

-- AI 快捷指令
INSERT INTO `quick_command` (`keyword`, `title`, `template`, `target_module`) VALUES
    ('查空教室',  '查空教室',   '帮我查询今天可用的空教室',          'library'),
    ('查校车时刻', '查校车时刻', '查询校车时刻表',                  'map'),
    ('预约图书馆', '预约图书馆', '帮我预约图书馆座位',              'library'),
    ('查校历',    '查校历',     '查询这学期的校历安排',             'life')
ON DUPLICATE KEY UPDATE `title` = VALUES(`title`);

-- Agent 工具注册表
INSERT INTO `agent_tool` (`name`, `description`, `endpoint`, `params_schema`) VALUES
    ('reserve_seat',  '预约图书馆座位', 'library.reserve', JSON_OBJECT('seat_id', 0, 'date', '', 'begin_time', '', 'end_time', '')),
    ('query_free_room','查询空教室',   'library.rooms',   JSON_OBJECT('campus', '', 'floor', '')),
    ('add_reminder',  '添加提醒',      'reminder.create', JSON_OBJECT('content', '', 'remind_at', '')),
    ('post_secondhand','发布闲置',     'secondhand.create', JSON_OBJECT('title', '', 'price', 0))
ON DUPLICATE KEY UPDATE `description` = VALUES(`description`);

-- 建筑与房间（示例）
INSERT INTO `building` (`id`, `name`, `campus`, `address`, `floor_count`, `latitude`, `longitude`) VALUES
    (1, '中心图书馆', '前卫南区', '图书馆路1号', 5, 43.880000, 125.320000),
    (2, '第二教学楼', '前卫南区', '前进大街2699号', 6, 43.881000, 125.322000)
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);

INSERT INTO `room` (`building_id`, `floor`, `name`, `room_type`, `capacity`, `has_power`, `is_classroom`) VALUES
    (1, 2, '二楼社科阅览室', 0, 120, 1, 0),
    (1, 3, '三楼自然科学阅览室', 0, 100, 1, 0),
    (2, 1, '101 教室', 2, 80, 0, 1),
    (2, 2, '201 教室', 2, 80, 0, 1)
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);

INSERT INTO `seat` (`room_id`, `seat_no`, `has_power`, `is_window`) VALUES
    (1, 'A01', 1, 1), (1, 'A02', 1, 0), (1, 'A03', 0, 1), (1, 'A04', 0, 0),
    (2, 'B01', 1, 1), (2, 'B02', 1, 0), (2, 'B03', 0, 1)
ON DUPLICATE KEY UPDATE `seat_no` = VALUES(`seat_no`);

-- 教室课表（示例：第二教学楼 101 周三下午空闲）
INSERT INTO `classroom_schedule` (`room_id`, `weekday`, `period`, `course_name`, `is_class`) VALUES
    (3, 1, 1, '高等数学', 1), (3, 1, 2, '高等数学', 1), (3, 1, 3, '', 0), (3, 1, 4, '', 0),
    (3, 3, 5, '', 0), (3, 3, 6, '', 0)
ON DUPLICATE KEY UPDATE `course_name` = VALUES(`course_name`);

-- 商家示例
INSERT INTO `merchant` (`name`, `category`, `address`, `delivery_fee`, `min_order`, `is_campus`) VALUES
    ('湖畔餐厅', '食堂', '中心湖畔', 0, 0, 1),
    ('学苑超市', '超市', '三公寓旁', 2, 10, 1)
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);

INSERT INTO `menu_item` (`merchant_id`, `name`, `description`, `price`) VALUES
    (1, '红烧肉套餐', '米饭+红烧肉+青菜', 15.00),
    (1, '牛肉拉面', '经典兰州风味', 12.00),
    (2, '农夫山泉', '550ml', 2.00)
ON DUPLICATE KEY UPDATE `name` = VALUES(`name`);

-- 校园通知示例
INSERT INTO `campus_notice` (`title`, `content`, `source`, `category`, `target_grade`) VALUES
    ('2026年秋季学期选课通知', '本学期选课将于 9月1日 开始，请同学们登录教务系统选课...', '教务处', '选课', ''),
    ('校医院开通网上挂号', '校医院支持微信预约挂号，具体流程见附件...', '后勤集团', '通知', '')
ON DUPLICATE KEY UPDATE `title` = VALUES(`title`);

-- RAG 知识库示例
INSERT INTO `knowledge_doc` (`title`, `category`, `content`, `source_url`) VALUES
    ('图书馆借阅规则', '图书馆', '读者凭校园卡借阅，本科生最多可借 10 册，借期 30 天...', 'https://lib.jlu.edu.cn/rules'),
    ('奖学金评定办法', '政策', '国家奖学金每年 9 月评定，须满足成绩排名前 10%...', 'https://xsc.jlu.edu.cn')
ON DUPLICATE KEY UPDATE `title` = VALUES(`title`);
