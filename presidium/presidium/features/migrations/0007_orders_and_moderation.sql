CREATE TABLE IF NOT EXISTS `market_orders` (
	`id` text PRIMARY KEY NOT NULL,
	`listing_id` text NOT NULL,
	`seller_id` text NOT NULL,
	`buyer_id` text NOT NULL,
	`locked_revision` integer NOT NULL,
	`locked_price_kopecks` integer NOT NULL,
	`commission_bps` integer NOT NULL,
	`commission_kopecks` integer NOT NULL,
	`vat_policy` text NOT NULL,
	`vat_kopecks` integer NOT NULL,
	`platform_net_kopecks` integer NOT NULL,
	`delivery_kopecks` integer,
	`payment_operator_kopecks` integer,
	`buyer_total_kopecks` integer NOT NULL,
	`seller_net_kopecks` integer NOT NULL,
	`status` text NOT NULL,
	`payment_mode` text DEFAULT 'sandbox' NOT NULL,
	`idempotency_key` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`listing_id`) REFERENCES `market_listings`(`id`) ON UPDATE no action ON DELETE restrict,
	FOREIGN KEY (`seller_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`buyer_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS `market_orders_idempotency_unique` ON `market_orders` (`buyer_id`, `idempotency_key`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_orders_buyer_updated` ON `market_orders` (`buyer_id`, `updated_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_orders_seller_updated` ON `market_orders` (`seller_id`, `updated_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_disputes` (
	`id` text PRIMARY KEY NOT NULL,
	`order_id` text NOT NULL,
	`initiator_id` text NOT NULL,
	`reason` text NOT NULL,
	`status` text NOT NULL,
	`resolution_note` text DEFAULT '' NOT NULL,
	`resolved_by` text,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`order_id`) REFERENCES `market_orders`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`initiator_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `market_disputes_order_idx` ON `market_disputes` (`order_id`, `created_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `market_moderation_log` (
	`id` text PRIMARY KEY NOT NULL,
	`listing_id` text NOT NULL,
	`moderator_id` text NOT NULL,
	`decision` text NOT NULL,
	`note` text DEFAULT '' NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`listing_id`) REFERENCES `market_listings`(`id`) ON UPDATE no action ON DELETE cascade
);
