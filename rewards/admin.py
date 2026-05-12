from django.contrib import admin

from .models import CustomerRewards, RewardTransaction


@admin.register(CustomerRewards)
class CustomerRewardsAdmin(admin.ModelAdmin):
    list_display = [
        "customer_email", "total_points", "tier",
        "lifetime_points_earned", "lifetime_points_redeemed", "updated_at",
    ]
    list_filter = ["tier"]
    search_fields = ["customer_email"]
    readonly_fields = [
        "lifetime_points_earned", "lifetime_points_redeemed",
        "created_at", "updated_at",
    ]


@admin.register(RewardTransaction)
class RewardTransactionAdmin(admin.ModelAdmin):
    list_display = [
        "id", "customer_email_display", "transaction_type",
        "points", "rental", "created_at",
    ]
    list_filter = ["transaction_type", "created_at"]
    search_fields = ["customer_rewards__customer_email", "reason"]
    readonly_fields = [
        "customer_rewards", "rental", "transaction_type",
        "points", "reason", "created_at",
    ]

    @admin.display(description="Customer Email")
    def customer_email_display(self, obj):
        return obj.customer_rewards.customer_email
