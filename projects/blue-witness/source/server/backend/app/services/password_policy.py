"""회원가입과 비밀번호 변경에 동일한 조합 조건을 적용함. 기존 로그인에는 적용하지 않음."""
import re
import string


def validate_password_policy(password: str) -> None:
    if not 10 <= len(password) <= 128:
        raise ValueError("비밀번호는 10~128자로 입력해주세요.")
    if not (re.search(r"[A-Z]", password) and re.search(r"[a-z]", password)
            and re.search(r"[0-9]", password)
            and any(character in string.punctuation for character in password)):
        raise ValueError("비밀번호에 영문 대문자·소문자·숫자·특수문자를 각각 1개 이상 포함해주세요.")
