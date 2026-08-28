# NPC 头像目录

仓库不包含正式 NPC 头像。生产部署请把图片放在外部目录，并设置
`DUEL_NPC_AVATARS_DIR`。人设 JSON 的可选 `avatar` 只能引用该目录根部的
png、jpg、jpeg、webp 或 gif 文件名；服务端会校验扩展名、真实文件和 resolve
后的父目录，拒绝路径穿越与越界符号链接。
