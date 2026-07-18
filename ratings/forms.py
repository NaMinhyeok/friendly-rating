import re

from django import forms
from django.contrib.auth import authenticate

from .models import Participant


class PinLoginForm(forms.Form):
    participant = forms.ModelChoiceField(
        label="이름",
        queryset=Participant.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
    )
    pin = forms.CharField(
        label="PIN 번호",
        min_length=4,
        max_length=4,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "inputmode": "numeric",
                "pattern": "[0-9]{4}",
                "placeholder": "4자리 PIN",
            }
        ),
    )

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.authenticated_user = None
        self.fields["participant"].queryset = Participant.objects.select_related(
            "user"
        ).order_by("slot")

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not re.fullmatch(r"\d{4}", pin):
            raise forms.ValidationError("PIN 번호는 숫자 4자리여야 합니다.")
        return pin

    def clean(self):
        cleaned_data = super().clean()
        participant = cleaned_data.get("participant")
        pin = cleaned_data.get("pin")
        if participant is None or pin is None:
            return cleaned_data

        user = authenticate(
            self.request,
            username=participant.user.get_username(),
            password=pin,
        )
        if user is None or not user.is_active:
            self.add_error("pin", "PIN 번호가 올바르지 않습니다.")
            return cleaned_data

        self.authenticated_user = user
        return cleaned_data
