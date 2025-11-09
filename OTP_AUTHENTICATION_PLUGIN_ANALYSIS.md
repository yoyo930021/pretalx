# Authentication Plugin 能否實現 OTP？深入分析

## 問題重述
pretalx 確實有 authentication plugin 機制，那麼這個機制能否用來實現 OTP 二次驗證？

## 簡短答案
**部分可行，但有重大限制**。現有的 authentication plugin 機制主要是為**替代性登入方式**（如 OAuth）設計，不是為**二次驗證**設計。

---

## 一、現有 Authentication Plugin 機制

### 1.1 機制說明
根據 `doc/developer/plugins/authentication.rst`：

```ini
# pretalx.cfg
[authentication]
additional_auth_backends=my_auth_plugin.auth.MyAuthBackend
```

```python
# 自訂 Django authentication backend
class MyAuthBackend:
    def authenticate(self, request, **credentials):
        # 驗證用戶
        return user or None

    def get_user(self, user_id):
        return User.objects.get(pk=user_id)
```

### 1.2 現有範例 Plugin
- **pretalx-socialauth**: OAuth2 登入 (GitHub, Google 等)
- **pretalx-oidc-auth**: OpenID Connect 登入

這些都是**替代性登入方式** - 用戶可選擇用帳密或用 OAuth 登入。

---

## 二、替代性登入 vs 二次驗證的差異

### 2.1 替代性登入 (OAuth, OIDC)
```
用戶選擇 → [帳密登入] 或 [OAuth 登入] → 成功 → 已登入
```

- **單一步驟**認證
- **互斥選擇** - 用戶選擇其中一種方式
- Django authentication backend 完美支援

### 2.2 二次驗證 (OTP/2FA)
```
用戶輸入 → [帳密] → 成功 → [OTP] → 成功 → 已登入
```

- **兩個步驟**認證
- **順序執行** - 必須先帳密再 OTP
- Django authentication backend **不是為此設計**

---

## 三、使用 Authentication Backend 實現 OTP 的挑戰

### 3.1 Django Authentication Backend 的限制

Django 的 `authenticate()` 方法設計為**單次調用**：

```python
# Django 的標準流程
user = authenticate(request, username='...', password='...')
if user:
    login(request, user)  # 直接登入
```

問題：
- ❌ 無法在密碼驗證後、登入前插入 OTP 驗證
- ❌ `authenticate()` 返回 `User` 或 `None`，沒有「部分成功」狀態

### 3.2 Pretalx 的 GenericLoginView 實作

```python
# src/pretalx/common/views/generic.py:150-154
def form_valid(self, form):
    pk = form.save()  # UserForm 內部呼叫 authenticate()
    user = User.objects.filter(pk=pk).first()
    login(self.request, user, backend="...")  # 立即登入
    return self.get_redirect()
```

問題：
- ❌ `UserForm.save()` 內部已完成 `authenticate()`
- ❌ `login()` 緊接著被呼叫，沒有插入點
- ❌ 即使 authentication backend 返回 `None`，也會被視為登入失敗，不會進入 OTP 流程

---

## 四、嘗試用 Authentication Backend 實現 OTP

### 方案 A: 在 authenticate() 中要求 OTP

```python
class OTPBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, otp_token=None, **kwargs):
        # 1. 驗證帳密
        user = super().authenticate(request, username, password, **kwargs)
        if not user:
            return None

        # 2. 檢查是否需要 OTP
        if user.otp_device.enabled:
            if not otp_token:
                return None  # ❌ 問題：沒有 OTP，返回 None = 登入失敗
            if not user.otp_device.verify(otp_token):
                return None  # OTP 錯誤

        return user
```

**問題**：
- ❌ 用戶第一次提交（只有帳密）會返回 `None` → 顯示「帳號密碼錯誤」（誤導）
- ❌ 無法告訴前端「帳密正確，請輸入 OTP」
- ❌ `UserForm` 不知道要顯示 OTP 欄位

### 方案 B: 修改 UserForm 加入 OTP 欄位

```python
# 透過 plugin 無法修改 UserForm
# UserForm 是核心程式碼，plugin 無法繼承或修改它
```

**問題**：
- ❌ Plugin 無法修改核心的 `UserForm`
- ❌ 沒有 `login_form` signal 可以擴展表單

---

## 五、Authentication Plugin 能做到什麼？

### 5.1 可以做到的

✅ **添加新的登入方式**
```python
@receiver(auth_html)
def add_oauth_button(sender, request, next_url=None, **kwargs):
    return '<a href="/oauth/login">Login with GitHub</a>'
```

✅ **完全替代帳密登入**
```python
class OAuthBackend:
    def authenticate(self, request, oauth_token=None, **kwargs):
        # 驗證 OAuth token
        return user
```

### 5.2 無法做到的

❌ **在帳密驗證後插入額外步驟**
- 沒有 hook point 可以攔截

❌ **修改登入表單**
- 沒有 `login_form` signal
- 無法動態添加 OTP 欄位

❌ **實現多步驟認證流程**
- Django authentication backend 是單步驟設計

---

## 六、結論：Authentication Plugin 的侷限性

### 6.1 為什麼不夠用？

| 需求 | Authentication Plugin 支援度 | 說明 |
|------|---------------------------|------|
| 替代性登入 (OAuth) | ✅ 完全支援 | 這是設計目的 |
| 二次驗證 (OTP) | ⚠️ 部分支援 | 需要繞道實現 |
| 修改登入表單 | ❌ 不支援 | 無對應 signal |
| 多步驟認證流程 | ❌ 不支援 | Backend 是單步驟設計 |

### 6.2 實現 OTP 仍需使用原報告的方案

即使有 authentication backend 機制，**仍然需要**：

**方案一 (短期)**: Custom Backend + **Session 管理** + **自訂視圖**
- ✅ Authentication backend: 標記用戶需要 OTP
- ✅ Session: 儲存中間狀態
- ✅ Middleware: 重導向到 OTP 驗證頁面
- ✅ 自訂視圖: 顯示 OTP 輸入表單

**方案二 (長期)**: 新增 **login_form Signal**
- 這才能真正解決問題
- 讓 OTP 欄位直接顯示在登入表單中
- 無需重導向，用戶體驗最佳

---

## 七、Authentication Backend 在 OTP 方案中的角色

### 7.1 可以作為輔助工具

```python
# 方案一中，backend 可用於標記狀態
class OTPBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username, password, **kwargs)
        if user and user.otp_device.enabled:
            # 標記需要 OTP，但不在這裡驗證
            request.session['otp_required'] = True
            request.session['pre_auth_user_id'] = user.pk
            return None  # 阻止登入
        return user
```

但仍需要：
- Middleware 攔截並重導向
- 自訂 OTP 驗證視圖
- Session 狀態管理

### 7.2 無法獨立完成 OTP

Authentication backend **不能單獨**實現完整的 OTP 功能，因為：
- 缺少 UI 整合（無法修改登入表單）
- 缺少流程控制（無法在兩步驟間轉換）
- 缺少狀態管理（Backend 是無狀態的）

---

## 八、最終回答

**Q: pretalx 的 authentication plugin 能實現 OTP 嗎？**

**A: 可以作為實作的一部分，但無法獨立完成。**

### 完整實現 OTP 需要：

1. ✅ **Authentication Backend** (部分使用)
   - 用途：標記需要 OTP 驗證
   - 限制：無法處理兩步驟流程

2. ✅ **自訂 Middleware** (必需)
   - 用途：攔截並重導向到 OTP 頁面

3. ✅ **自訂視圖 + 模板** (必需)
   - 用途：顯示 OTP 輸入表單

4. ✅ **Session 管理** (必需)
   - 用途：儲存中間狀態

5. ✅ **OTP Device Model** (必需)
   - 用途：儲存用戶的 OTP secret

### 這正是原報告「方案一」的內容

原報告的分析是正確的：
- ✅ Authentication plugin 機制存在
- ✅ 但它不足以實現完整的 OTP 功能
- ✅ 需要額外的 middleware、視圖、session 管理
- ✅ 最佳方案仍是向核心貢獻 `login_form` signal

---

## 九、補充：Authentication Backend 文件的不足之處

當前文件 (`doc/developer/plugins/authentication.rst`) 只涵蓋：
- ✅ 替代性登入方式 (OAuth, OIDC)
- ❌ **沒有**二次驗證的範例
- ❌ **沒有**多步驟認證的指引

建議文件改進：
1. 明確說明 authentication backend 的使用場景
2. 添加「不適用於二次驗證」的警告
3. 或者，提供二次驗證的實作指引（使用 session + middleware）

---

**結論**: pretalx 的 authentication plugin 機制很好地支援了 OAuth/OIDC 等替代性登入方式，但對於 OTP/2FA 這類二次驗證，需要結合其他技術（middleware, session, custom views）才能實現。原報告的四個方案分析仍然有效且必要。
