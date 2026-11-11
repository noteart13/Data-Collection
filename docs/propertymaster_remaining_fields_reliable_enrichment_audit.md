# Audit nguồn làm giàu các trường PropertyMaster còn thiếu

Ngày kiểm tra: 2026-11-03

## Kết luận

Có thể làm giàu đáng tin cậy thêm nhiều trường, nhưng không thể đạt độ phủ 100% bằng dữ liệu mở. Phương án trong arena đúng về hướng phân rã nguồn dữ liệu, nhưng các tỷ lệ `88%`, `98%`, `100%` và một số phép gán “exact” chưa có chứng cứ. Tournament score chỉ đánh giá câu trả lời so với các đối thủ; nó không xác nhận nguồn dữ liệu bên ngoài.

Nguyên tắc triển khai an toàn:

1. Chỉ ghi giá trị quan sát trực tiếp khi nguồn có thể đối chiếu đúng địa chỉ/lot/listing ID.
2. Giá trị mô hình hoặc công thức phải đi vào trường `estimate`, không ghi đè trường thực tế.
3. Mỗi giá trị phải có `sourceUrl` hoặc `sourceRecordId`, `observedAt/asOf`, `evidenceHash` và loại bằng chứng.
4. Không có bằng chứng thì giữ `null`; không dùng default để tạo cảm giác đầy đủ.

## Kiểm định các tuyên bố chính của giải pháp arena

| Tuyên bố | Phán quyết | Bằng chứng và điều chỉnh cần thiết |
|---|---|---|
| Listing archive phủ khoảng 88% NSW | Chưa xác minh | Không tìm thấy báo cáo chính thức của Domain, PropTrack hoặc Cotality chứng minh tỷ lệ này ở cấp property. Chỉ sử dụng tỷ lệ sau khi nhà cung cấp giao coverage report và pilot exact-address. |
| CV/OCR mặt bằng cho độ chính xác 100% | Sai | Nghiên cứu floor-plan thực tế báo cáo metric dưới 100% và chỉ ra vấn đề tổng quát hóa. CV/OCR chỉ là candidate extraction; cần confidence threshold và kiểm tra người hoặc nguồn thứ hai trước khi ghi. |
| Strata Plan cho diện tích từng lot | Đúng có điều kiện | NSW LRS yêu cầu floor plan thể hiện diện tích từng lot/part-lot và tổng diện tích lot. Nhưng diện tích có thể là approximate, có thể bao gồm nhiều phần như chỗ đỗ xe/ban công, và NSW LRS nói rõ nó có thể khác usable/rental floor area. Phải lưu `strataLotArea`, không tự đổi thành `internalArea`. |
| Unit entitlement dùng để suy diện tích | Sai | Unit entitlement là số nguyên do valuer phân bổ theo market value, không phải tỷ lệ m². Không dùng để nội suy diện tích hoặc số phòng. |
| Một PDF Strata Plan luôn giá $14–16, khoảng $0.30/unit | Sai nếu coi là giá chung | NSW LRS xác nhận plan image là sản phẩm trả phí qua authorised broker, nhưng broker tự đặt giá; một broker được ủy quyền hiện niêm yết từ $20.50. Mức $0.30 chỉ là phép chia minh họa cho scheme lớn, không đúng với scheme nhỏ và chưa gồm OCR/QA/licence. |
| Final OC cho construction year chính xác 100% | Đúng có điều kiện | Open PCC metadata chỉ bao phủ hồ sơ được lodge trên Portal từ 01/2019; council bắt buộc dùng Portal từ 01/07/2021 và dữ liệu cảnh báo hồ sơ cũ có thể thiếu. Partial OC, staged work, change of use và renovation không phải original construction year. Integration API còn cần subscription key. |
| Aerial imagery cho construction year chính xác | Chỉ là ước tính | Ảnh theo thời gian có thể tạo `estimatedCompletionWindow`; không được ghi vào `constructionYear` như chứng thư pháp lý. |
| Public Rental Bond Board cho exact rent từng căn, bằng bond/4 | Sai | Dataset mở chỉ công bố postcode, dwelling type, weekly rent và bedroom count; không có địa chỉ/property ID. Bond không bắt buộc và mức tối đa là 4 tuần, nên phép chia 4 không luôn đúng. |
| Council bill tính chính xác hơn 98% từ land value | Chưa được chứng minh | Council có thể dùng ad-valorem, ad-valorem + base, hoặc ad-valorem subject to minimum; còn có category/subcategory, special rates, waste/stormwater/service charges, rebates và adjustments. Công thức chỉ tạo `ordinaryRateEstimate` nếu có đủ policy đúng financial year và exact assessed land value. |
| Sydney Water cố định khoảng $185.45/quý, đồng nhất | Sai và đã lỗi thời | Giá 2026–27 gồm water service $26.65, wastewater $189.88 và stormwater $9.67/unit nếu áp dụng; fixed total phổ biến vì vậy là $216.53 hoặc $226.20, chưa gồm usage. Unmetered water và authority ngoài Sydney Water còn khác. |
| Không có listing sau 60 ngày thì status=`off_market` đã verified | Sai về ngữ nghĩa | Quy tắc 60/90 ngày của REA liên quan eligibility cho nhãn “New”; Domain off-market là provider action. Không thấy listing chỉ chứng minh `lastSeenAt` đã cũ. Nên lưu `unknown/inferred_off_market`, không gắn verified. |
| REA profile URL luôn sinh được từ slug G-NAF | Chưa xác minh và không nên dùng | Slug không phải API contract; có thể redirect, đổi canonical hoặc không tồn tại. Chỉ lưu URL do provider API/trang canonical trả về, kèm provider property/listing ID. |

## Bản đồ làm giàu có thể tin cậy

| Trường đích hiện tại | Nguồn chấp nhận | Giá trị nên lưu | Khi nào giữ null |
|---|---|---|---|
| `bed`, `bath` | Licensed listing/property feed; listing archive có exact-address + provider ID; DA/floorplan được kiểm chứng | Số quan sát và ngày quan sát | Chỉ có OCR confidence thấp, match mơ hồ hoặc dữ liệu suburb-level |
| `features.garage` | Listing feed hoặc purchased Strata Plan xác định phần lot/car space | `garage/carSpacesObserved` và bằng chứng lot mapping | Car space là common property/exclusive-use by-law chưa xác định |
| `features.indoorFeatures` | Structured listing flags hoặc mô tả listing | `observedFeatures` kèm listing ID và ngày | NLP chỉ suy từ văn bản mơ hồ hoặc tin quá cũ không còn phù hợp |
| `constructionYear` | Final OC đúng công trình | Năm OC; lưu cả certificate ID | Chỉ có DP/SP registration date hoặc aerial estimate |
| `area.totalArea` | Strata plan lot area hoặc floorplan legend được kiểm tra | Nên tách `strataLotArea`, `internalArea`, `balconyArea`, `carSpaceArea` | Không phân tách được các part-lot hoặc chỉ có unit entitlement |
| `pricing.weeklyRent` | Lease/bond record được phép truy cập và exact-address; listing feed | Tách `contractedWeeklyRent` và `advertisedWeeklyRent` | Chỉ có postcode median hoặc không có tenancy |
| `pricing.rentalEstimate` | AVM có version, as-of, low/high, confidence | Khoảng ước tính | Model không có coverage hoặc input property match yếu |
| `pricing.councilBill` | Actual rate notice/authorised council query | `actualCouncilBill` | Không có notice; công thức phải chuyển sang `ordinaryRateEstimate` |
| `pricing.waterBillPeriod` | Actual bill hoặc address-aware tariff calculator | `actualWaterBill` hoặc các fixed service components | Không biết authority, services, meter/stormwater configuration |
| `status`, `listingOption` | Active licensed listing feed/agency webhook | Trạng thái quan sát có TTL | Chỉ suy từ việc không tìm thấy listing |
| `realestate_url` | Canonical URL trong provider response | URL + provider ID + listing/property type | URL tự dựng chưa được resolve/canonicalize |

## Schema tối thiểu nên tách

```json
{
  "construction": {
    "occupationCertificateDate": null,
    "schemeRegisteredDate": null,
    "estimatedCompletionWindow": null
  },
  "area": {
    "internalArea": null,
    "strataLotArea": null,
    "balconyArea": null,
    "carSpaceArea": null,
    "unit": "sqM"
  },
  "pricing": {
    "contractedWeeklyRent": null,
    "advertisedWeeklyRent": null,
    "rentalEstimate": {"low": null, "high": null, "asOf": null},
    "actualCouncilBill": null,
    "ordinaryRateEstimate": null,
    "actualWaterBill": null,
    "fixedWaterServiceEstimate": null
  },
  "market": {
    "status": "unknown",
    "observedAt": null,
    "expiresAt": null
  },
  "links": {
    "provider": null,
    "propertyId": null,
    "profileUrl": null,
    "listingId": null,
    "listingUrl": null
  }
}
```

## Lộ trình triển khai thực tế

### P0 — sửa cổng tin cậy trước khi thêm nguồn

- Không cho `status=off_market` và `listingOption=off_market` mặc định được xem là verified.
- Xóa tariff tĩnh `$185.45 / quarter` khỏi đường ghi trusted; tariff phải có authority, component và financial year.
- Không ghi `weeklyRent` từ suburb median hoặc rental AVM; tách trường estimate.
- Không dựng URL portal; chỉ giữ canonical URL đã resolve.

### P1 — nguồn thương mại cho bed/bath/status/URL

Thử một licensed API/feed (Domain Developer, Cotality/PropTrack theo hợp đồng) trên 100–500 property, exact-address matching. Chỉ triển khai rộng sau khi đo:

- exact match rate;
- field coverage theo house/unit;
- age/staleness;
- conflict rate giữa các listing;
- quyền lưu trữ và tái phân phối.

### P2 — Strata Plan pilot theo scheme

Mua 3–5 scheme plan đã biết, trong đó có `SP106542`, rồi kiểm tra thủ công:

- lot-to-unit mapping;
- total lot area và các part-lot;
- car space thuộc cùng lot, lot riêng hay common property;
- tỷ lệ OCR đúng;
- chi phí thực sau broker/API/licence.

Không ghi Qdrant trong pilot. Xuất candidate JSON và report đối chiếu trước.

### P3 — OC, council và water

- Xin quyền/API hoặc dùng dữ liệu council/certifier được cấp phép cho OC; tách full/partial OC.
- Xây council policy table có financial year, category, minimum/base/ad-valorem, special rates và service charges. Kết quả công thức luôn là estimate.
- Dùng address-aware utility tariff/service lookup; actual bill chỉ từ tài liệu được ủy quyền.

## Rủi ro hiện có trong repository

- `app/schemas/property_schema.py` đang mặc định `status` và `listingOption` thành `off_market`.
- `app/operations/portal_profile_hydrator.py` đang chứa NSW tariff tĩnh `$185.45 / quarter`, không còn phù hợp với bảng giá hiện hành.
- `bulk_enrich_master.py` đã có phần fail-closed tốt hơn cho URL và evidence, nhưng schema hiện tại vẫn gộp actual/advertised/estimated rent và actual/estimated bills.

## Nguồn chính

- NSW LRS Strata Plans Preparation Guide: https://www.nswlrs.com.au/assets/f/1129775276948026/x/29c6ad4200/strata-plans-preparation-guide.pdf
- NSW LRS record access and authorised brokers: https://live.nswlrs.com.au/services/record-searches/how-to-find-a-record/
- NSW Planning Portal APIs: https://www.planningportal.nsw.gov.au/API
- NSW Online PCC open data: https://data.nsw.gov.au/data/dataset/online-pcc-data-api
- NSW certification data reporting: https://www.planningportal.nsw.gov.au/news/clarification-certification-data-reporting
- NSW Fair Trading Rental Bond Lodgement dataset: https://data.nsw.gov.au/data/dataset/rental-bond-lodgement
- NSW Fair Trading bond guidance: https://www.fairtrading.nsw.gov.au/__data/assets/pdf_file/0019/374005/Topic_4_Bonds_and_other_costs.pdf
- NSW Office of Local Government rates guidance: https://www.olg.nsw.gov.au/public/laws-and-regulations/rates-charges-and-pensioner-concessions
- NSW Council Rating and Revenue Raising Manual: https://www.olg.nsw.gov.au/sites/default/files/2026-02/council-rating-and-revenue-raising-manual.pdf
- Sydney Water residential pricing 2026–27: https://www.sydneywater.com.au/accounts-billing/paying-your-bill/our-prices/residential-pricing.html
- Domain API listing example: https://developer.domain.com.au/docs/latest/apis/pkg_property/references/listings_detailedresidentialsearch/
- Domain off-market API semantics: https://developer.domain.com.au/docs/v2/apis/pkg_listing_management/guides/offmarket-listings/
- Cotality property attribute definition/API offering: https://www.cotality.com/au/our-data
- REA Property Profiles and missing profiles: https://help.realestate.com.au/hc/en-us/articles/9393344786969-How-Property-Profiles-work
- REA terms of use: https://about.realestate.com.au/terms-use/
- Example authorised LRS broker pricing: https://www.fynd.info/products
- Floor-plan OCR/CV limitations: https://openaccess.thecvf.com/content/CVPR2021/papers/Lv_Residential_Floor_Plan_Recognition_and_Reconstruction_CVPR_2021_paper.pdf

