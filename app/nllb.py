from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(
    "facebook/nllb-200-distilled-600M", token=True, src_lang="eng_Latn"
)
model = AutoModelForSeq2SeqLM.from_pretrained("facebook/nllb-200-distilled-600M", token=True)
article = "Designer gourmet gas kitchen with stylish benchtop and ample cupboard space, two generous sized bedrooms with built-in robes and plush carpets, two sublime bathrooms with ensuite off main, custom built-in cupboards/storage space throughout, internal laundry, intercom, air-conditioning/heating and additional private storage cage. Amenities, including concierge, breathtaking outdoor 20m heated pool overlooking the city, small gym, outdoor seating, landscaped entertaining areas with BBQ facilities. "
inputs = tokenizer(article, return_tensors="pt")
#zho_Hant
#
#kor_Hang
#jpn_Jpan
#tha_Thai
#ind_Latn
#zul_Latn south affrica
#xho_Latn south affrica
translated_tokens = model.generate(
    **inputs, forced_bos_token_id=tokenizer.convert_tokens_to_ids("vie_Latn"), max_length=300
)
temp = tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)[0]

print(temp)
