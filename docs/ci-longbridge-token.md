# CI Longbridge Token

## 背景

`Update Data and Deploy Pages` workflow 需要 Longbridge auth 才能抓 13F、行情和 K 线。

不要把 `longbridge auth login` 生成的 `~/.longbridge/openapi/cli-auth` 直接放进 GitHub Secrets。`cli-auth` 是机器绑定加密文件，复制到 GitHub Actions runner 后会报：

```text
Failed to decrypt auth token
```

CI 应保存 SDK OAuth plaintext token 文件：

```text
~/.longbridge/openapi/tokens/<client_id>
```

Runner 启动后会把这个 legacy token 迁移成 runner 自己可解密的 `cli-auth`。

## Client ID

使用 Longbridge CLI 内置 client id：

```bash
CLIENT_ID="fd52fbc5-02a9-47f5-ad30-0842c841aae9"
```

不要使用自己临时注册的 OAuth client id。CLI v0.23 会按内置 client id 查找和迁移 legacy token；其他 client id 文件存在也会被判定为未登录。

## 更新 Token

1. 生成 SDK token 文件：

```bash
CLIENT_ID="fd52fbc5-02a9-47f5-ad30-0842c841aae9"
uv run --with longbridge python -c 'from longbridge.openapi import OAuthBuilder; import webbrowser; OAuthBuilder("'$CLIENT_ID'").build(webbrowser.open)'
```

浏览器授权完成后，应生成：

```text
~/.longbridge/openapi/tokens/fd52fbc5-02a9-47f5-ad30-0842c841aae9
```

2. 可选：用 clean HOME 验证 token 能被 CLI 迁移并认证：

```bash
tmpdir="$(mktemp -d)"
mkdir -p "$tmpdir/.longbridge/openapi/tokens"
cp "$HOME/.longbridge/openapi/tokens/$CLIENT_ID" "$tmpdir/.longbridge/openapi/tokens/$CLIENT_ID"
chmod 600 "$tmpdir/.longbridge/openapi/tokens/$CLIENT_ID"
HOME="$tmpdir" longbridge check --format json
```

期望输出中包含：

```json
{"session":{"token":"valid"}}
```

3. 更新 GitHub Secrets：

```bash
gh secret set LONGBRIDGE_CLIENT_ID --repo jjyr/value-tracker --body "$CLIENT_ID"
gh secret set LONGBRIDGE_TOKEN_FILE_B64 --repo jjyr/value-tracker --body "$(base64 -i "$HOME/.longbridge/openapi/tokens/$CLIENT_ID" | tr -d '\n')"
```

4. 手动重跑数据部署：

```bash
gh workflow run schedule.yml -f mode=weekly --ref main
```

5. 检查 run：

```bash
gh run list --workflow schedule.yml --limit 3
```

确认 `Check Longbridge auth`、`Run data job`、`Deploy Pages` 都成功。

## 常见错误

- `Refresh token has expired`: 重新执行本页的更新 token 流程。
- `Failed to decrypt auth token`: 错把 `cli-auth` 放进 secret；改用 `tokens/<client_id>`。
- `Not authenticated. Please run 'longbridge auth login' first.`: client id 不对，或 token 文件没有放在 `tokens/fd52fbc5-02a9-47f5-ad30-0842c841aae9`。
