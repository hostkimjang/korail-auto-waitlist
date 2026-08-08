"""Secret-safe value contracts shared by KORAIL Pydoll authentication layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class KorailLoginMethod(StrEnum):
    MEMBERSHIP_NUMBER = "membership_number"
    EMAIL = "email"
    PHONE = "phone"

    @property
    def tab_selector(self) -> str:
        return {
            self.MEMBERSHIP_NUMBER: "button#memberNo[type='button']",
            self.EMAIL: "button#email[type='button']",
            self.PHONE: "button#phone[type='button']",
        }[self]

    @property
    def identity_selector(self) -> str:
        return {
            self.MEMBERSHIP_NUMBER: (
                "input#id[name='id'][type='text'][title='회원번호'][maxlength='10']"
            ),
            self.EMAIL: "input#id[name='id'][type='email'][title='이메일 주소']",
            self.PHONE: ("input#id[name='id'][type='text'][title='휴대폰 번호'][maxlength='11']"),
        }[self]


@dataclass(frozen=True, repr=False)
class KorailCredentialInput:
    login_id: str = field(repr=False)
    password: str = field(repr=False)
    version: str
    login_method: KorailLoginMethod = KorailLoginMethod.MEMBERSHIP_NUMBER
