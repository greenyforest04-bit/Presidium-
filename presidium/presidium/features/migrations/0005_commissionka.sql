CREATE TABLE `market_favorites` (
	`user_id` text NOT NULL,
	`listing_id` text NOT NULL,
	`created_at` integer NOT NULL,
	PRIMARY KEY(`user_id`, `listing_id`),
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`listing_id`) REFERENCES `market_listings`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `market_images` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`listing_id` text,
	`full_digest` text NOT NULL,
	`thumb_digest` text,
	`size` integer NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`owner_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`listing_id`) REFERENCES `market_listings`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `market_image_owner_time` ON `market_images` (`owner_id`,`created_at`);--> statement-breakpoint
CREATE INDEX `market_image_listing` ON `market_images` (`listing_id`);--> statement-breakpoint
CREATE TABLE `market_listings` (
	`id` text PRIMARY KEY NOT NULL,
	`seller_id` text NOT NULL,
	`category` text NOT NULL,
	`condition` text NOT NULL,
	`seller_type` text NOT NULL,
	`title` text NOT NULL,
	`description` text NOT NULL,
	`city` text NOT NULL,
	`search_text` text NOT NULL,
	`city_search` text NOT NULL,
	`ownership` text DEFAULT 'unverified' NOT NULL,
	`price_kopecks` integer NOT NULL,
	`specs` text NOT NULL,
	`photo_ids` text NOT NULL,
	`status` text DEFAULT 'pending' NOT NULL,
	`condition_verified` integer DEFAULT 0 NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	`request_digest` text NOT NULL,
	FOREIGN KEY (`seller_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX `market_catalog_time` ON `market_listings` (`status`,`created_at`,`id`);--> statement-breakpoint
CREATE INDEX `market_catalog_price` ON `market_listings` (`status`,`price_kopecks`,`id`);--> statement-breakpoint
CREATE INDEX `market_owner` ON `market_listings` (`seller_id`,`status`);--> statement-breakpoint
CREATE TABLE `market_price_history` (
	`listing_id` text NOT NULL,
	`revision` integer NOT NULL,
	`price_kopecks` integer NOT NULL,
	`at` integer NOT NULL,
	PRIMARY KEY(`listing_id`, `revision`),
	FOREIGN KEY (`listing_id`) REFERENCES `market_listings`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `market_sellers` (
	`user_id` text PRIMARY KEY NOT NULL,
	`identity` text DEFAULT 'unverified' NOT NULL,
	`manufacturer` text DEFAULT 'unverified' NOT NULL,
	`matrix_user_id` text,
	`matrix_base_url` text,
	FOREIGN KEY (`user_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `market_seller_matrix` ON `market_sellers` (`matrix_base_url`,`matrix_user_id`);