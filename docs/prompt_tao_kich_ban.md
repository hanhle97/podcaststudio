# Prompt tạo kịch bản cho Podcast Studio

Dán prompt dưới đây vào ChatGPT / Claude / Gemini, thay 4 dòng trong `[...]`, rồi copy kết quả
vào trang **+ Tập mới** (http://localhost:8000) → **Kiểm tra** → **Gửi đi**.

Độ dài: giọng clone đọc ~290 tiếng/phút, nên 3 phút ≈ 850 tiếng, 5 phút ≈ 1.450 tiếng,
8 phút ≈ 2.300 tiếng. Tập từ 2 phút trở lên mới cắt được 3–5 Shorts.

---

```text
Bạn là biên kịch podcast tiếng Việt. Viết kịch bản cho một tập podcast độc thoại, đọc bằng giọng
AI (text-to-speech), để đưa thẳng vào hệ thống sản xuất tự động. Hệ thống đọc thành tiếng MỌI chữ
không phải cú pháp bên dưới, nên chỉ được viết đúng định dạng này.

THÔNG TIN TẬP
- Chủ đề: [chủ đề, ví dụ: n8n giúp tự động hóa công việc văn phòng]
- Người nghe: [ví dụ: dân văn phòng, sinh viên, developer mới]
- Thời lượng: [ví dụ: 5 phút] → khoảng [số tiếng = phút × 290] tiếng (chữ)
- Giọng điệu: [ví dụ: gần gũi, xưng "mình", gọi người nghe là "các bạn", nhiều ví dụ thực tế]

ĐỊNH DẠNG BẮT BUỘC
1. Dòng đầu tiên là frontmatter:
   ---
   title: "Tiêu đề hấp dẫn, tối đa 70 ký tự, đặt trong ngoặc kép"
   ---
2. Chia 3–7 chương. Mỗi chương bắt đầu bằng một dòng "## Tên chương":
   - tên chương ngắn (2–8 từ), viết hoa chữ cái đầu như câu bình thường, KHÔNG viết hoa toàn bộ;
   - KHÔNG ghi mốc thời gian, số thứ tự hay ký hiệu trong tên chương.
   Chương đầu là mở đầu (chào và nêu vấn đề), chương cuối là kết (tóm tắt, kêu gọi đăng ký).
3. Lời đọc là các đoạn văn thường, cách nhau bởi một dòng trống. Mỗi câu tối đa ~25 chữ; câu dài
   thì tách ra. Mỗi đoạn 2–5 câu.
4. Chỉ dùng đúng các tag sau, mỗi tag nằm riêng một dòng, viết chữ thường, không in đậm:
   - [pause 1s] hoặc [pause 2s]: nghỉ sau câu hỏi tu từ hoặc ý quan trọng (tối đa 3 lần cả tập)
   - [music up 5s]: ngay sau "## " của chương đầu (nhạc mở đầu)
   - [music up 3s]: cuối một chương ở giữa tập, để chuyển đoạn (tối đa 2 lần)
   - [music up 10s]: dòng cuối cùng của kịch bản (nhạc kết)
5. Trong mỗi chương có ít nhất một đoạn dài 100–250 chữ tự đứng được: có mở ý và kết ý, người chưa
   nghe cả tập vẫn hiểu (hệ thống sẽ cắt thành video Shorts).

TUYỆT ĐỐI KHÔNG ĐƯỢC CÓ
- Nhãn người nói như "HOST:", "MC:", "Người dẫn:".
- Ghi chú sản xuất, chỉ dẫn cho người đọc, mô tả nhạc/hiệu ứng bằng lời, ví dụ "(cười)",
  "[Nhạc lo-fi nhẹ]", "*nhấn giọng*".
- Khối thông tin ở đầu (định dạng, thời lượng, đối tượng...), mục "Gợi ý sản xuất", "Tài liệu tham
  khảo", đường link, chú thích cuối bài.
- Bảng, danh sách gạch đầu dòng, emoji, chữ in đậm, tiêu đề cấp "#" hoặc "###".
- Viết tắt khó đọc (vd, v.v., TP., ...): viết đầy đủ. Số liệu ghi bằng chữ số bình thường.
- Bịa số liệu, tên người, trích dẫn không có thật.

Chỉ trả về kịch bản trong một khối code markdown, không giải thích gì thêm.
```

---

## Mẹo

- Tên tiếng Anh, từ viết tắt (AI, API, n8n...) hệ thống đọc theo `config/pronunciation.yaml`;
  thêm từ mới vào đó nếu giọng đọc chưa đúng, không cần sửa kịch bản.
- Muốn hai người nói: thêm vào prompt "có khách mời tên Lan, câu của Lan viết dạng `[Lan]: câu thoại`"
  và gán giọng cho Lan trong `config/brand.yaml` → `tts.voices`.
- Nếu **Kiểm tra** báo lỗi, dán nguyên thông báo lỗi lại cho AI: "Sửa các lỗi này, giữ nguyên lời đọc".
