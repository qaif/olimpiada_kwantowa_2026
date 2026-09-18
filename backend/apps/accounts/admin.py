"""Panel administracyjny kont.

Kod zaproszenia nie jest w bazie ani w adminie – widoczny jest wyłącznie skrót sha256.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import (
    CommitteeMember,
    ConsentDefinition,
    ConsentRecord,
    InvitationCode,
    Membership,
    Participant,
    Region,
    RegistrationProfile,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name")}),
        (
            _("Permissions"),
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


class ConsentRecordInline(admin.TabularInline):
    """Historia zgód przy profilu uczestnika – wyłącznie do czytania.

    Wpis dowodowy nie jest polem konfiguracji: poprawiony ręcznie przestaje być dowodem czegokolwiek.
    Zgody zapisują serwisy (``accounts.services.record_consents`` i ``set_publish_name_consent``),
    a admin ma je pokazać – po to, żeby przy pytaniu „na co ta osoba się zgodziła” dało się
    odpowiedzieć bez zaglądania do bazy.
    """

    model = ConsentRecord
    extra = 0
    can_delete = False
    fields = ("kind", "document_version", "given_at", "withdrawn_at", "source")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ConsentDefinition)
class ConsentDefinitionAdmin(admin.ModelAdmin):
    """Definicje zgód konkursu – narzędzie **operatora platformy**, nie koordynatora.

    Koordynator zmienia wersję dokumentu w swoim panelu (``/coordinator/consents/``), bo tam zmiana
    wymaga potwierdzenia zdaniem „od tej chwili nowe zgody będą zapisywane pod wersją X” i zostawia
    wpis ``consent_definition.version_changed`` w audycie (``apps.accounts.consents.change_version``).
    Tutaj wiersz da się obejrzeć i poprawić po imporcie – **bez** tego wpisu, więc to jest droga
    dla operatora naprawiającego dane, a nie dla organizatora prowadzącego konkurs.

    Konkurs jest polem tylko do odczytu po założeniu wiersza: przeniesienie definicji do innego
    konkursu nie jest edycją, tylko podmianą treści oświadczenia u kogoś innego.
    """

    list_display = ("competition", "kind", "field_name", "version", "required", "is_active")
    list_filter = ("competition", "kind", "required", "required_for_minor", "is_active")
    search_fields = ("field_name", "text", "version")
    ordering = ("competition", "ordering", "id")

    def get_readonly_fields(self, request, obj=None):
        return ("competition",) if obj is not None else ()


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    """Podział terytorialny konkursu – narzędzie **operatora platformy**, nie koordynatora.

    Koordynator dostaje własny ekran (``/coordinator/regions/``, zadanie T20). Tutaj wiersz da się
    obejrzeć i poprawić po imporcie albo po migracji – dlatego konkurs jest po założeniu tylko do
    odczytu: przeniesienie regionu do innego konkursu zmieniałoby okręg ludziom, którzy już go
    zadeklarowali, i to bez śladu w audycie.
    """

    list_display = ("competition", "code", "name", "level", "parent", "position", "is_active")
    list_filter = ("competition", "level", "is_active", "counts_for_conflict")
    search_fields = ("code", "name")
    ordering = ("competition", "position", "name", "id")

    def get_readonly_fields(self, request, obj=None):
        return ("competition",) if obj is not None else ()


@admin.register(RegistrationProfile)
class RegistrationProfileAdmin(admin.ModelAdmin):
    """Profil rejestracji konkursu – narzędzie **operatora platformy**, nie koordynatora.

    Wiersza nie ma żaden konkurs, dopóki ktoś go tu (albo komendą) nie założy: brak wiersza znaczy
    „jak dziś” i taki jest stan Konkursu #1 (§ 1.3.4). Dlatego lista bywa pusta i to jest stan
    poprawny, a nie brakujące dane.

    Konkurs jest po założeniu tylko do odczytu: przeniesienie profilu do innego konkursu zmieniłoby
    formularz rejestracji u kogoś innego – i to bez śladu w audycie.
    """

    list_display = (
        "competition",
        "allowed_institution_types",
        "allow_free_text_school",
        "allow_foreign",
        "require_grade",
    )
    list_filter = ("allow_custom_directory", "allow_free_text_school", "allow_foreign")
    ordering = ("competition",)

    def get_readonly_fields(self, request, obj=None):
        return ("competition",) if obj is not None else ()


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    """Role w konkursach – narzędzie **operatora platformy**, nie koordynatora.

    Koordynator nadaje role w swoim panelu (i tam zapisuje się też grupa Django, patrz
    ``apps.accounts.services.grant_role``). Tutaj wiersz da się obejrzeć i poprawić ręcznie po
    imporcie albo po backfillu – dlatego ``granted_at``/``granted_by`` są tylko do odczytu:
    to jest zapis, **kiedy i przez kogo** rola powstała, a nie pole konfiguracji.
    """

    list_display = ("user", "competition", "role", "granted_at", "granted_by")
    list_filter = ("competition", "role")
    search_fields = ("user__email",)
    readonly_fields = ("granted_at", "granted_by")
    autocomplete_fields = ("user",)


@admin.register(Participant)
class ParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "public_code",
        "user",
        "school",
        "school_ref",
        "grade",
        "district",
        "birth_year",
        "publish_full_name",
    )
    list_filter = ("district", "grade", "guardian_consent", "publish_full_name")
    search_fields = ("public_code", "user__email", "school")
    readonly_fields = ("public_code", "gdpr_consent_at", "terms_accepted_at")
    autocomplete_fields = ("user",)
    inlines = (ConsentRecordInline,)
    # Słownik ma ponad osiem tysięcy wierszy – zwykły ``<select>`` wysyłałby je wszystkie
    # do przeglądarki przy każdym otwarciu profilu uczestnika.
    raw_id_fields = ("school_ref",)


@admin.register(CommitteeMember)
class CommitteeMemberAdmin(admin.ModelAdmin):
    list_display = ("user", "district", "district_verified", "status", "is_appeals_committee", "approved_at")
    list_filter = ("status", "is_appeals_committee", "district_verified", "district")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "approved_at", "approved_by")
    autocomplete_fields = ("user",)


@admin.register(InvitationCode)
class InvitationCodeAdmin(admin.ModelAdmin):
    """Kodów nie da się podejrzeć ani wygenerować z admina – od tego jest ``manage.py create_invitation``."""

    list_display = (
        "code_hash_short",
        "email",
        "created_by",
        "grants_status",
        "is_appeals",
        "district",
        "used_count",
        "max_uses",
        "sent_at",
        "expires_at",
        "revoked_at",
    )
    list_filter = ("grants_status", "is_appeals", "district")
    # Adres wysyłki jest tu jedynym sposobem, żeby odpowiedzieć na pytanie „czy ta osoba dostała
    # od nas kod” – skrótu sha256 nie da się wyszukać po niczym, co człowiek ma w ręku.
    search_fields = ("code_hash", "email")
    readonly_fields = ("code_hash", "created_by", "created_at", "used_count", "sent_at")

    @admin.display(description="sha256 kodu")
    def code_hash_short(self, obj: InvitationCode) -> str:
        return f"{obj.code_hash[:12]}…"

    def has_add_permission(self, request) -> bool:
        return False
