CREATE TABLE IF NOT EXISTS `social_media` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`post_id` text,
	`story_id` text,
	`kind` text NOT NULL,
	`mime` text NOT NULL,
	`file_name` text NOT NULL,
	`byte_size` integer NOT NULL,
	`sha256` text NOT NULL,
	`alt_text` text DEFAULT '' NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`owner_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_media_owner_time` ON `social_media` (`owner_id`, `created_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_media_post` ON `social_media` (`post_id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_media_story` ON `social_media` (`story_id`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `social_posts` (
	`id` text PRIMARY KEY NOT NULL,
	`author_id` text NOT NULL,
	`channel_id` text,
	`text` text NOT NULL,
	`media_ids` text DEFAULT '[]' NOT NULL,
	`topics` text DEFAULT '[]' NOT NULL,
	`status` text DEFAULT 'published' NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`request_digest` text NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`author_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_posts_feed_cursor` ON `social_posts` (`status`, `created_at`, `id`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_posts_author` ON `social_posts` (`author_id`, `status`, `created_at`);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `social_stories` (
	`id` text PRIMARY KEY NOT NULL,
	`author_id` text NOT NULL,
	`text` text DEFAULT '' NOT NULL,
	`color` text DEFAULT 'blue' NOT NULL,
	`background` text DEFAULT '#17171c' NOT NULL,
	`layout` text DEFAULT 'grid' NOT NULL,
	`duration` integer DEFAULT 8 NOT NULL,
	`media_json` text DEFAULT '[]' NOT NULL,
	`layers_json` text DEFAULT '[]' NOT NULL,
	`poll_json` text,
	`status` text DEFAULT 'active' NOT NULL,
	`revision` integer DEFAULT 1 NOT NULL,
	`request_digest` text NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`author_id`) REFERENCES `users`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_stories_active` ON `social_stories` (`status`, `expires_at`, `created_at`);
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS `social_stories_author` ON `social_stories` (`author_id`, `status`, `expires_at`);
