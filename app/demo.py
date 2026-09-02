"""デモ用のシードデータ。設計書 §3-1「6人・3拠点の定例」に合わせる。"""

from __future__ import annotations

from app.domain.models import UserProfile

# 西（立川・三鷹）・南（横浜）・東（千葉・船橋）・北（大宮）の4方向に散らした構成。
# この散らばりだと sum / minimax / cost の3ポリシーで結論が割れ、
# 「どの基準で選んだか」を宣言する意味がデモで見える。
SEED_USERS: list[UserProfile] = [
    UserProfile(uid="tanaka", display_name="田中", origin_station="立川", chat_id="U-tanaka"),
    UserProfile(uid="sato", display_name="佐藤", origin_station="三鷹", chat_id="U-sato"),
    UserProfile(uid="suzuki", display_name="鈴木", origin_station="横浜", chat_id="U-suzuki"),
    UserProfile(uid="takahashi", display_name="高橋", origin_station="千葉", chat_id="U-takahashi"),
    UserProfile(uid="ito", display_name="伊藤", origin_station="船橋", chat_id="U-ito"),
    UserProfile(uid="watanabe", display_name="渡辺", origin_station="大宮", chat_id="U-watanabe"),
]

SAMPLE_REQUEST = "来週火曜14時、この6人で。2時間、ホワイトボードのある部屋で。いちばん遠い人の負担を減らしたい"
