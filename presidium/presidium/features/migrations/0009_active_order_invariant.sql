CREATE TRIGGER IF NOT EXISTS `__migration_0009_orders_guard`
BEFORE UPDATE ON `market_orders`
FOR EACH ROW
WHEN OLD.`status` = NEW.`status`
 AND OLD.`delivery_kopecks` IS NEW.`delivery_kopecks`
 AND OLD.`payment_operator_kopecks` IS NEW.`payment_operator_kopecks`
BEGIN
	SELECT CASE
		WHEN EXISTS (
			SELECT `listing_id` FROM `market_orders`
			WHERE `status` IN ('confirmed', 'paid_sandbox', 'paid', 'shipped', 'disputed')
			GROUP BY `listing_id` HAVING COUNT(*) > 1
		) THEN RAISE(ABORT, 'MIGRATION_0009_CONFLICT_DUPLICATE_ACTIVE_ORDERS: В таблице market_orders найдены дублирующиеся активные заказы на одно объявление. Автоматическое удаление запрещено. Выполните диагностику (node scripts/check-and-remediate-0009.mjs) и переведите лишний дубликат в статус cancelled.')
		WHEN EXISTS (
			SELECT 1 FROM `market_orders`
			WHERE `delivery_kopecks` IS NULL OR `payment_operator_kopecks` IS NULL
		) THEN RAISE(ABORT, 'MIGRATION_0009_CONFLICT_NULL_ORDER_FEES: В таблице market_orders найдены заказы с NULL в delivery_kopecks или payment_operator_kopecks. Выполните диагностику (node scripts/check-and-remediate-0009.mjs) перед повторным запуском миграции 0009.')
	END;
END;
--> statement-breakpoint
UPDATE `market_orders` SET `updated_at` = `updated_at` WHERE `id` = (SELECT `id` FROM `market_orders` LIMIT 1);
--> statement-breakpoint
DROP TRIGGER IF EXISTS `__migration_0009_orders_guard`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `__migration_0009_disputes_guard`
BEFORE UPDATE ON `market_disputes`
FOR EACH ROW
WHEN OLD.`status` = NEW.`status`
BEGIN
	SELECT CASE
		WHEN EXISTS (
			SELECT `order_id` FROM `market_disputes`
			WHERE `status` = 'open'
			GROUP BY `order_id` HAVING COUNT(*) > 1
		) THEN RAISE(ABORT, 'MIGRATION_0009_CONFLICT_DUPLICATE_OPEN_DISPUTES: В таблице market_disputes найдено несколько открытых споров для одного заказа. Закройте дублирующиеся споры перед миграцией 0009.')
	END;
END;
--> statement-breakpoint
UPDATE `market_disputes` SET `updated_at` = `updated_at` WHERE `id` = (SELECT `id` FROM `market_disputes` LIMIT 1);
--> statement-breakpoint
DROP TRIGGER IF EXISTS `__migration_0009_disputes_guard`;
--> statement-breakpoint
ALTER TABLE `market_listings` ADD `active_order_id` text;
--> statement-breakpoint
UPDATE `market_listings`
SET `active_order_id` = (
	SELECT `o`.`id` FROM `market_orders` `o`
	WHERE `o`.`listing_id` = `market_listings`.`id`
	  AND `o`.`status` IN ('confirmed', 'paid_sandbox', 'paid', 'shipped', 'disputed')
	ORDER BY `o`.`created_at` DESC
	LIMIT 1
)
WHERE `status` = 'sold' AND `active_order_id` IS NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_listings_active_order_idx` ON `market_listings` (`active_order_id`);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `market_orders_one_active_per_listing` ON `market_orders` (`listing_id`) WHERE `status` IN ('confirmed', 'paid_sandbox', 'paid', 'shipped', 'disputed');
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `market_disputes_one_open_per_order` ON `market_disputes` (`order_id`) WHERE `status` = 'open';
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_orders_require_confirmed_fees_insert`
BEFORE INSERT ON `market_orders`
FOR EACH ROW
WHEN NEW.`delivery_kopecks` IS NULL OR NEW.`payment_operator_kopecks` IS NULL
BEGIN
	SELECT RAISE(ABORT, 'NOT NULL constraint failed: market_orders.delivery_kopecks / payment_operator_kopecks');
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `market_orders_require_confirmed_fees_update`
BEFORE UPDATE ON `market_orders`
FOR EACH ROW
WHEN NEW.`delivery_kopecks` IS NULL OR NEW.`payment_operator_kopecks` IS NULL
BEGIN
	SELECT RAISE(ABORT, 'NOT NULL constraint failed: market_orders.delivery_kopecks / payment_operator_kopecks');
END;
