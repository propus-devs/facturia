from django.contrib import admin
from django.contrib.admin.decorators import register
from clients.models import Client, CustomUser, Role
from django.contrib.auth.admin import UserAdmin

@register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('name', 'tax_id', 'is_active')
    search_fields = ('name', 'tax_id')

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'client', 'get_roles')
    search_fields = ('client__name', 'username', 'first_name', 'last_name', 'email')

    fieldsets = UserAdmin.fieldsets + (
        ('Client & Roles', {'fields': ('client', 'roles')}),
    )

    # El alta de UserAdmin solo pide usuario y contraseñas; el cliente es
    # obligatorio (FK + CustomUser.clean), así que hay que pedirlo aquí.
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('client', 'username', 'email', 'usable_password', 'password1', 'password2'),
        }),
    )

    filter_horizontal = ('roles',)  # útil para ManyToMany

    def get_roles(self, obj):
        return ", ".join([r.name for r in obj.roles.all()])
    get_roles.short_description = "Roles"


admin.site.register(Role)
