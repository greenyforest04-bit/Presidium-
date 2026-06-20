"""Mobile UI components for Presidium.

Provides responsive, touch-friendly UI components optimized for mobile
devices (iOS/Android). These components adapt their layout and interaction
patterns based on screen size and touch input availability.

Components:
    - MobileNavigation: Bottom navigation bar with FAB
    - SwipeContainer: Swipe gesture handler for navigation
    - MobileChatRoom: Mobile-optimized chat interface
    - MobileSidebar: Drawer-based sidebar for mobile
    - TouchOptimized: Mixin for touch-friendly sizing/spacing

Design principles:
    - Minimum touch target: 44x44 px (Apple HIG) / 48x48 dp (Material)
    - Edge swipe from left: open sidebar
    - Edge swipe from right: go back
    - Swipe on message: reply/forward actions
    - Bottom sheet for contextual menus (not right-click)
    - FAB for primary actions (new chat, new group)
    - Pull-to-refresh for message sync
    - Adaptive layout: stack on narrow screens, side-by-side on wide
"""

from presidium.ui.mobile.navigation import MobileNavigation
from presidium.ui.mobile.swipe import SwipeContainer, SwipeDirection
from presidium.ui.mobile.chat_room import MobileChatRoom
from presidium.ui.mobile.sidebar import MobileSidebar
from presidium.ui.mobile.touch import TouchOptimizedMixin

__all__ = [
    "MobileNavigation",
    "SwipeContainer",
    "SwipeDirection",
    "MobileChatRoom",
    "MobileSidebar",
    "TouchOptimizedMixin",
]
