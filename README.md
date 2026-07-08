# mail-mihari

メール受信箱を5分おきに確認し、フリマ・オークションサイトの売却通知を検出してスマホ(ntfy)に知らせる。

- 実行: GitHub Actions (`.github/workflows/check.yml`) が5分間隔で `checker.py` を実行
- 必要なSecrets: `BIGLOBE_PASSWORD`(メールパスワード) / `NTFY_TOPIC`(通知チャンネル名)
- `state.json` は「どのメールまで確認したか」の記録(自動更新)

注意: リポジトリに60日間コミットがないとGitHubが定期実行を自動停止する。その場合はActionsタブから手動で再有効化する。
