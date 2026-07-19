import re
from typing import cast

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
                "data-pin-input": "",
            }
        ),
    )

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.authenticated_user = None
        participant_field = cast(
            forms.ModelChoiceField,
            self.fields["participant"],
        )
        participant_field.queryset = Participant.objects.select_related(
            "user"
        ).order_by("slot")

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not re.fullmatch(r"\d{4}", pin):
            raise forms.ValidationError("PIN 번호는 숫자 4자리여야 합니다.")
        return pin

    def clean(self):
        cleaned_data = super().clean() or {}
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


class ScoreChangeForm(forms.Form):
    class Operation:
        INCREASE = "increase"
        DECREASE = "decrease"

    operation = forms.ChoiceField(
        label="변경 방향",
        choices=(
            (Operation.INCREASE, "올리기"),
            (Operation.DECREASE, "내리기"),
        ),
        widget=forms.RadioSelect,
    )
    amount = forms.IntegerField(
        label="변경할 점수",
        min_value=1,
        max_value=100,
        widget=forms.NumberInput(
            attrs={
                "inputmode": "numeric",
                "min": "1",
                "max": "100",
                "placeholder": "점수 입력",
            }
        ),
    )
    reason = forms.CharField(
        label="이유 (선택)",
        max_length=200,
        required=False,
        strip=True,
        error_messages={
            "max_length": "변경 이유는 200자 이하여야 합니다.",
        },
        widget=forms.Textarea(
            attrs={
                "rows": "3",
                "maxlength": "200",
                "placeholder": "남기고 싶은 이유가 있다면 적어 주세요.",
            }
        ),
    )

    @property
    def delta(self):
        amount = self.cleaned_data["amount"]
        if self.cleaned_data["operation"] == self.Operation.DECREASE:
            return -amount
        return amount
