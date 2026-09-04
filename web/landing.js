/* 基準の選択。真ん中が選択中で、左右は控えに回るキャラ選択。
   無限に回す部分（複製をどこに何枚置いて、いつ黙って戻すか）は
   自前で持つと折り返しで必ず飛ぶので、Swiper に任せる。
   大小と色づけは CSS 側（.swiper-slide-active）でやる。 */

if (window.Swiper) {
  /* Swiper のループは、画面に出る枚数より十分に多い札がないと働かない。
     3枚だと足りず「slides is not enough for loop mode」で止まるので、
     同じ3枚をあと2組ぶん並べておく。
     複製は読み上げとタブ移動から外す（同じ札が何度も読まれるため）。 */
  const wrapper = document.querySelector(".picks .swiper-wrapper");
  const base = [...wrapper.children];
  for (let round = 0; round < 2; round += 1) {
    base.forEach((el) => {
      const copy = el.cloneNode(true);
      copy.setAttribute("aria-hidden", "true");
      copy.tabIndex = -1;
      wrapper.append(copy);
    });
  }

  new Swiper(".picks", {
    loop: true,
    centeredSlides: true,
    // 'auto' だとループの枚数計算が合わず「slides is not enough」で止まる。
    // 枚数で指定して、幅は Swiper に決めさせる。
    slidesPerView: 1.35,
    breakpoints: {
      640: { slidesPerView: 2.2 },
      900: { slidesPerView: 2.6 },
    },
    spaceBetween: 16,
    speed: 420,
    // 開いたときは「バランスよく」を真ん中に置く（ヒーローの図と揃える）
    initialSlide: 1,
    slideToClickedSlide: true,
    keyboard: { enabled: true },
    navigation: { prevEl: "#pick-prev", nextEl: "#pick-next" },
    a11y: {
      prevSlideMessage: "前の基準",
      nextSlideMessage: "次の基準",
    },
  });
}
