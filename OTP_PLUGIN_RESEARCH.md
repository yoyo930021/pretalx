# OTP 登入 Plugin 研究報告

## 執行摘要

本研究探討如何透過 plugin 形式在 pretalx 中實現 OTP (One-Time Password) 二次驗證登入功能。經過深入分析程式碼架構，發現**目前的 plugin 系統尚未完全支援擴展登入流程**，但有多種實作方案可行，其中最佳方案是**擴充核心系統以支援登入表單的 plugin 整合**。

---

## 一、現況分析

### 1.1 登入系統架構

#### 核心元件

**登入視圖 (GenericLoginView)**
- 位置: `src/pretalx/common/views/generic.py:108-155`
- 使用 `UserForm` 處理登入和註冊
- 流程:
  ```
  用戶提交表單 → UserForm 驗證 → authenticate() → login() → 重導向
  ```

**登入表單 (UserForm)**
- 位置: `src/pretalx/person/forms/user.py`
- 整合登入與註冊功能於同一表單
- 驗證邏輯:
  - `_clean_login()`: 使用 Django 的 `authenticate()` 驗證帳密
  - `_clean_register()`: 檢查密碼強度和信箱唯一性

**關鍵程式碼片段** (`GenericLoginView.form_valid()` 在 153 行):
```python
def form_valid(self, form):
    pk = form.save()
    user = User.objects.filter(pk=pk).first()
    login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")
    return self.get_redirect()
```

這個方法直接在密碼驗證成功後立即呼叫 `login()`，**沒有提供插入點讓 plugin 進行額外驗證**。

### 1.2 Plugin 系統架構

#### Signal 機制

pretalx 使用自訂的 `EventPluginSignal` 類別，只向已啟用的 plugin 發送訊號。

**現有的表單擴展 Signal** (位於 `src/pretalx/orga/signals.py`):
- `speaker_form` (line 117): 擴展講者資料表單
- `submission_form` (line 131): 擴展投稿表單
- `review_form` (line 144): 擴展審核表單
- `mail_form` (line 159): 擴展郵件表單

這些 signal 都使用 `FormSignalMixin` 處理額外表單:

```python
class FormSignalMixin:
    extra_forms_signal = None

    def form_valid(self, form):
        result = super().form_valid(form)  # 主表單先儲存
        for f in self.extra_forms:
            if f.is_valid():
                f.save()  # Plugin 表單隨後儲存
        return result
```

**認證相關 Signal** (位於 `src/pretalx/common/signals.py:241-254`):
- `auth_html`: 在登入頁面加入自訂 HTML 內容
  - 用途: 顯示額外的登入方式連結 (如 OAuth)
  - 限制: **僅能加入 UI 元素，無法整合到表單驗證流程**

#### 關鍵發現: 登入流程的 Plugin 整合缺口

1. **沒有 `login_form` signal**: 無法像其他表單一樣擴展登入表單
2. **沒有 `pre_login` / `post_authenticate` signal**: 無法在認證完成後、登入前插入額外驗證
3. **`auth_html` signal 不足**: 只能加入 HTML，不能修改驗證邏輯

---

## 二、OTP Plugin 實作方案

### 方案一: 使用 Custom Authentication Backend + Session 狀態管理 ⭐⭐⭐⭐

#### 概念
創建自訂 Django authentication backend，在密碼驗證後要求 OTP 驗證，使用 session 儲存中間狀態。

#### 實作架構

```python
# Plugin structure
pretalx_otp/
├── __init__.py
├── apps.py                 # Plugin metadata
├── models.py               # OTPDevice model (儲存用戶的 OTP secret)
├── auth.py                 # Custom authentication backend
├── forms.py                # OTP 驗證表單
├── views.py                # OTP 驗證視圖
├── urls.py                 # Plugin URL routes
└── signals.py              # Signal receivers
```

#### 核心實作

**1. 自訂 Authentication Backend** (`auth.py`):
```python
from django.contrib.auth.backends import ModelBackend

class OTPAuthenticationBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, otp_token=None, **kwargs):
        # 先用標準方式驗證帳密
        user = super().authenticate(request, username, password, **kwargs)
        if user is None:
            return None

        # 檢查用戶是否啟用 OTP
        if not hasattr(user, 'otp_device') or not user.otp_device.enabled:
            return user  # 未啟用 OTP，直接返回

        # 如果已啟用 OTP 但未提供 token，設定 session 標記
        if otp_token is None:
            request.session['otp_pre_authenticated_user_id'] = user.pk
            request.session['otp_required'] = True
            return None  # 返回 None 表示認證尚未完成

        # 驗證 OTP token
        if user.otp_device.verify_token(otp_token):
            return user

        return None
```

**2. OTP Device Model** (`models.py`):
```python
import pyotp
from django.db import models
from pretalx.person.models import User

class OTPDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='otp_device')
    secret = models.CharField(max_length=32)
    enabled = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)

    def verify_token(self, token):
        totp = pyotp.TOTP(self.secret)
        return totp.verify(token, valid_window=1)

    def get_provisioning_uri(self):
        return pyotp.totp.TOTP(self.secret).provisioning_uri(
            name=self.user.email,
            issuer_name='pretalx'
        )
```

**3. 修改登入流程 - 使用 Middleware**:

由於無法直接修改 `GenericLoginView`，我們需要使用 middleware 攔截認證流程:

```python
# middleware.py
from django.shortcuts import redirect
from django.urls import reverse

class OTPMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 檢查是否需要 OTP 驗證
        if request.session.get('otp_required'):
            # 如果當前不在 OTP 驗證頁面，重導向過去
            if not request.path.startswith('/p/otp/'):
                return redirect(reverse('plugins:pretalx_otp:verify'))

        return self.get_response(request)
```

**4. OTP 驗證視圖** (`views.py`):
```python
from django.contrib.auth import login
from django.shortcuts import redirect
from django.views.generic import FormView

class OTPVerifyView(FormView):
    template_name = 'pretalx_otp/verify.html'
    form_class = OTPForm

    def dispatch(self, request, *args, **kwargs):
        # 確保用戶已通過第一階段認證
        if not request.session.get('otp_required'):
            return redirect('cfp:event.login')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user_id = self.request.session.get('otp_pre_authenticated_user_id')
        user = User.objects.get(pk=user_id)

        # 驗證 OTP
        if user.otp_device.verify_token(form.cleaned_data['token']):
            # 清除 session 標記
            del self.request.session['otp_required']
            del self.request.session['otp_pre_authenticated_user_id']

            # 登入用戶
            login(self.request, user, backend='django.contrib.auth.backends.ModelBackend')

            # 重導向到原本的目標
            return redirect(self.request.session.get('next', '/'))

        form.add_error('token', 'Invalid OTP token')
        return self.form_invalid(form)
```

#### 優點
✅ 不需要修改 pretalx 核心程式碼
✅ 完全通過 plugin 實現
✅ 支援選擇性啟用 (用戶可選擇是否啟用 OTP)
✅ 遵循 Django 標準的 authentication backend 模式

#### 缺點
❌ 需要自訂 middleware，可能與其他 plugin 衝突
❌ Session 狀態管理複雜，需要仔細處理邊界情況
❌ 用戶體驗較差 (需要重導向到另一個頁面輸入 OTP)
❌ 難以整合到原生登入表單

#### 實作難度: 中等

---

### 方案二: 擴充 Core 支援 `login_form` Signal ⭐⭐⭐⭐⭐ (推薦)

#### 概念
在 pretalx 核心新增 `login_form` signal，讓 plugin 可以像擴展其他表單一樣擴展登入表單。這是**最乾淨、最符合 pretalx 設計哲學**的方案。

#### 需要的核心修改

**1. 新增 Signal 定義** (`src/pretalx/common/signals.py`):
```python
login_form = django.dispatch.Signal()
"""
This signal allows plugins to add custom form elements to the login form,
such as OTP fields or other authentication factors.

The signal is called with the ``request`` keyword argument.
Receivers may return either a single form or a list of forms.

Unlike EventPluginSignal, this is a regular Django signal since login
can occur outside of event context (e.g., /orga/login).
"""
```

**2. 修改 GenericLoginView 使用 FormSignalMixin** (`src/pretalx/common/views/generic.py`):

```python
class GenericLoginView(FormSignalMixin, FormView):  # 加入 FormSignalMixin
    form_class = UserForm
    extra_forms_signal = "pretalx.common.signals.login_form"  # 指定 signal

    # ... 保持其他程式碼不變 ...

    def form_valid(self, form):
        pk = form.save()
        user = User.objects.filter(pk=pk).first()

        # 在 login 前先驗證 extra_forms
        for f in self.extra_forms:
            if not f.is_valid():
                # OTP 驗證失敗
                if f.errors:
                    messages.error(self.request, f.errors)
                return self.form_invalid(form)

        # 所有驗證通過，執行登入
        login(self.request, user, backend="django.contrib.auth.backends.ModelBackend")

        # 儲存 plugin forms (例如記錄 OTP 使用)
        for f in self.extra_forms:
            f.save()

        return self.get_redirect()
```

**3. 修改登入模板** (`src/pretalx/common/templates/common/auth.html`):

在登入表單中加入 extra_forms 的渲染:

```django
<div class="auth-form-block">
    <h4 class="text-center">{% translate "I already have an account" %}</h4>
    {{ form.login_email.as_field_group }}
    {{ form.login_password.as_field_group }}

    {# Plugin 額外欄位 #}
    {% for extra_form in extra_forms %}
        {% if extra_form.label %}
            <h5>{{ extra_form.label }}</h5>
        {% endif %}
        {{ extra_form.as_p }}
    {% endfor %}

    <button type="submit" class="btn btn-lg btn-success btn-block">
        {% translate "Log in" %}
    </button>
</div>
```

#### Plugin 實作範例

```python
# pretalx_otp/signals.py
from django.dispatch import receiver
from pretalx.common.signals import login_form
from .forms import OTPForm

@receiver(login_form)
def add_otp_field(sender, request, **kwargs):
    """在登入表單加入 OTP 欄位"""
    # 只在用戶輸入帳密後才顯示 OTP 欄位
    if request.POST.get('login_email'):
        from pretalx.person.models import User
        try:
            user = User.objects.get(email__iexact=request.POST.get('login_email'))
            if hasattr(user, 'otp_device') and user.otp_device.enabled:
                # 用戶啟用了 OTP，返回 OTP 表單
                return OTPForm(data=request.POST if request.method == 'POST' else None, user=user)
        except User.DoesNotExist:
            pass
    return None
```

```python
# pretalx_otp/forms.py
from django import forms
from django.core.exceptions import ValidationError

class OTPForm(forms.Form):
    label = "Two-Factor Authentication"

    otp_token = forms.CharField(
        max_length=6,
        min_length=6,
        label="Authentication Code",
        widget=forms.TextInput(attrs={
            'autocomplete': 'one-time-code',
            'inputmode': 'numeric',
            'pattern': '[0-9]{6}',
            'placeholder': '000000'
        })
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_otp_token(self):
        token = self.cleaned_data['otp_token']
        if not self.user.otp_device.verify_token(token):
            raise ValidationError("Invalid authentication code.")
        return token

    def save(self):
        # 記錄 OTP 使用
        self.user.otp_device.last_used = timezone.now()
        self.user.otp_device.save()
```

#### 優點
✅ **最乾淨的解決方案**，完全符合 pretalx 的設計模式
✅ 與現有的 `speaker_form`、`submission_form` 等一致
✅ 用戶體驗最佳 - OTP 欄位直接顯示在登入表單中
✅ 不需要 middleware 或 session 管理
✅ 支援多個 plugin 同時擴展登入表單
✅ 程式碼可測試性高

#### 缺點
❌ **需要修改 pretalx 核心程式碼**
❌ 需要向 pretalx 專案提交 Pull Request
❌ 等待上游接受和發布需要時間

#### 實作難度: 低 (核心修改簡單) + 流程時間 (等待 PR 審核)

---

### 方案三: JavaScript 前端整合 + 自訂 View ⭐⭐

#### 概念
使用 `auth_html` signal 插入 JavaScript 程式碼，攔截登入表單提交，先向 plugin 的自訂 endpoint 驗證 OTP，再允許表單提交。

#### 實作概要

```python
# signals.py
@receiver(auth_html)
def inject_otp_script(sender, request, **kwargs):
    return """
    <script>
    document.addEventListener('DOMContentLoaded', function() {
        const loginForm = document.getElementById('auth-form');
        loginForm.addEventListener('submit', async function(e) {
            const email = document.querySelector('[name="login_email"]').value;

            // 檢查用戶是否需要 OTP
            const response = await fetch('/p/otp/check/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({email: email})
            });

            const data = await response.json();
            if (data.otp_required && !document.querySelector('[name="otp_token"]')) {
                e.preventDefault();
                // 顯示 OTP 輸入欄位
                showOTPInput();
            }
        });
    });
    </script>
    """
```

#### 優點
✅ 不需要修改核心程式碼
✅ 可以提供較好的用戶體驗 (不用重導向)

#### 缺點
❌ **嚴重依賴 JavaScript**，無障礙性差
❌ 安全性較低 - 可繞過前端驗證直接提交表單
❌ 需要額外的 API endpoint
❌ 實作複雜且容易出錯
❌ 難以維護

#### 實作難度: 高
#### ⚠️ 不推薦

---

### 方案四: 使用 Django 的 `user_logged_in` Signal + 強制登出 ⭐⭐⭐

#### 概念
監聽 Django 的 `user_logged_in` signal，在用戶登入後立即檢查是否需要 OTP，如果需要但未驗證則強制登出並重導向。

#### 實作

```python
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.contrib.auth import logout
from django.dispatch import receiver
from django.shortcuts import redirect

@receiver(user_logged_in)
def check_otp_on_login(sender, request, user, **kwargs):
    # 檢查用戶是否啟用 OTP
    if hasattr(user, 'otp_device') and user.otp_device.enabled:
        # 檢查是否已驗證 OTP
        if not request.session.get('otp_verified'):
            # 儲存用戶資訊並登出
            request.session['otp_pending_user_id'] = user.pk
            logout(request)
            # 重導向到 OTP 驗證頁面
            # (需要在 middleware 中處理重導向)
```

#### 優點
✅ 實作相對簡單
✅ 不需要修改核心程式碼

#### 缺點
❌ 用戶體驗差 - 先登入再登出，困惑
❌ 可能與其他 `user_logged_in` signal receivers 衝突
❌ 需要額外的 middleware 處理重導向
❌ Session 管理複雜

#### 實作難度: 中等
#### ⚠️ 不推薦 (用戶體驗差)

---

## 三、方案比較

| 方案 | 推薦度 | 需修改核心 | 用戶體驗 | 安全性 | 可維護性 | 實作難度 |
|------|--------|-----------|---------|--------|---------|---------|
| 方案一: Custom Backend + Session | ⭐⭐⭐⭐ | ❌ | 中 (需重導向) | 高 | 中 | 中 |
| 方案二: login_form Signal (推薦) | ⭐⭐⭐⭐⭐ | ✅ | 高 (無縫整合) | 高 | 高 | 低 |
| 方案三: JavaScript 前端整合 | ⭐⭐ | ❌ | 中 | 低 | 低 | 高 |
| 方案四: user_logged_in Hook | ⭐⭐⭐ | ❌ | 低 (先登入再登出) | 中 | 低 | 中 |

---

## 四、建議實作路徑

### 短期方案 (1-2 週)
採用**方案一: Custom Backend + Session**，完全通過 plugin 實現，無需修改核心:

1. 建立 `pretalx-otp` plugin 基本架構
2. 實作 `OTPDevice` model
3. 建立 OTP 驗證視圖和表單
4. 實作 middleware 進行流程攔截
5. 在用戶設定頁面加入 OTP 管理介面 (使用 `profile_bottom_html` signal)

### 長期方案 (1-3 個月)
向 pretalx 核心貢獻**方案二: login_form Signal**:

1. Fork pretalx repository
2. 實作 `login_form` signal:
   - 在 `common/signals.py` 新增 signal 定義
   - 修改 `GenericLoginView` 繼承 `FormSignalMixin`
   - 更新登入模板支援 `extra_forms`
3. 撰寫測試案例
4. 更新文件 (plugin development guide)
5. 提交 Pull Request 到 pretalx
6. 等待審核和合併
7. 使用新的 signal 重寫 OTP plugin (更簡潔的實作)

### 建議優先順序
1. **立即開始**: 方案一 (可馬上使用)
2. **同時進行**: 準備方案二的 PR (為長期考慮)
3. **PR 合併後**: 遷移到方案二 (更好的維護性)

---

## 五、技術細節補充

### 5.1 相關檔案位置

| 元件 | 檔案路徑 |
|------|---------|
| 登入視圖 | `src/pretalx/common/views/generic.py:108-155` |
| 登入表單 | `src/pretalx/person/forms/user.py` |
| 登入模板 | `src/pretalx/common/templates/common/auth.html` |
| Signal 定義 | `src/pretalx/common/signals.py` |
| Orga Signal 定義 | `src/pretalx/orga/signals.py` |
| FormSignalMixin | `src/pretalx/common/views/generic.py:48-89` |
| User Model | `src/pretalx/person/models/user.py` |

### 5.2 現有的 FormSignalMixin 運作流程

```python
# 1. View 定義 signal
class SpeakerDetail(OrgaCRUDView):
    extra_forms_signal = "pretalx.orga.signals.speaker_form"

# 2. Mixin 載入 plugin forms
@cached_property
def extra_forms(self):
    signal = import_string(self.extra_forms_signal)
    forms = signal.send_robust(sender=request.event, ...)
    return forms

# 3. 主表單儲存後，plugin forms 才儲存
def form_valid(self, form):
    result = super().form_valid(form)  # 主 instance 先儲存
    for f in self.extra_forms:
        f.save()  # Plugin forms 使用已儲存的 instance
    return result
```

這個模式非常適合套用到登入流程。

### 5.3 OTP 技術選擇

推薦使用 **TOTP (Time-based One-Time Password)**:
- 標準: RFC 6238
- Python 套件: `pyotp`
- 相容 Google Authenticator、Authy 等主流 app
- 無需網路連線

### 5.4 安全考量

1. **Secret 儲存**: 使用 Django 的加密欄位儲存 OTP secret
2. **Rate Limiting**: 限制 OTP 驗證失敗次數 (防暴力破解)
3. **Backup Codes**: 提供恢復碼以防用戶遺失裝置
4. **Session 安全**:
   - 設定短暫的 session timeout
   - 使用 secure cookie flags
5. **Timing Attack**: 使用 constant-time comparison 驗證 token

---

## 六、結論

### 核心發現
pretalx 的 plugin 系統設計優良，已支援多種擴展點，但**登入流程是目前唯一尚未支援 plugin 整合的主要表單**。最近的 commits 顯示 pretalx 團隊正在完善表單 signal 機制，這是提出 `login_form` signal 的絕佳時機。

### 最佳實作策略
**雙軌並進**:
1. 使用方案一快速實現 OTP plugin (可立即使用)
2. 向 pretalx 提出 PR 新增 login_form signal (長期改善)

這樣既能滿足短期需求，也能為 pretalx 社群貢獻有價值的功能，使未來所有需要擴展登入流程的 plugin 都能受益。

### 後續行動
- [ ] 決定採用的方案
- [ ] 建立 `pretalx-otp` plugin 專案
- [ ] (如採用方案二) Fork pretalx 並準備 PR
- [ ] 實作核心功能
- [ ] 撰寫測試
- [ ] 撰寫文件

---

**研究完成日期**: 2025-11-09
**當前分支**: `claude/otp-login-plugin-research-011CUx851LvkktegvqBuqLcs`
