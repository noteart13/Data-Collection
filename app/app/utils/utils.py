import os
import json
from pathlib import Path
import numpy as np
import pathlib
import sys
import re
from dateutil import parser
class NumpyEncoder(json.JSONEncoder):
    """ Special json encoder for numpy types """
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return json.JSONEncoder.default(self, obj)


def read_json(json_file):
    with open(json_file, "r") as read_f:
        decodedArray = json.load(read_f)
    return decodedArray
def get_json_files():
    filepath = os.getenv("DATA_NSW_PATH", str(APP_DIR / "data" / "NSW"))
    jsons = []
    for subdir, dirs, files in os.walk(filepath):
        for file in files:
            filepath = subdir + os.sep + file
            if filepath.endswith(".json"):
                jsons.append(filepath)
    return jsons
sys.path.append("..")
APP_DIR = pathlib.Path(__file__).parent.absolute()

# Next set the project root directory.
PROJECT_DIR = APP_DIR.parent

def get_all_suburb_city():
    json_lst = [ pathlib.Path(APP_DIR,"suburb_city_mapping_v1_NSW_ACT.json"),
                pathlib.Path(APP_DIR,"suburb_city_mapping_v1_QLD_NT_WA.json"),
                pathlib.Path(APP_DIR,"suburb_city_mapping_v1_VIC_SA_TAS.json")]
    all_suburb_city = []
    for json in json_lst:
        lst = read_json(json)
        all_suburb_city.extend(lst)
    return all_suburb_city
def change_key(old_key, new_key, input_data):
    if isinstance(input_data, dict):
        input_data[new_key] = input_data.pop(old_key)
    elif isinstance(input_data, list):
        for item in input_data:
            item[new_key] = item.pop(old_key)
    return input_data


def json_dumps(meta, name_json):
    save_path = os.getenv("MAPPING_SAVE_PATH", str(APP_DIR / "suburb_city_mapping_v2_all.json"))
    pred_file = Path(os.path.join(save_path, name_json))
    with open(pred_file, "w") as f:
        json.dump(meta, f, cls=NumpyEncoder)

def dict_sweep(input_dict, key):
    if isinstance(input_dict, dict):
        return {k: dict_sweep(v, key) for k, v in input_dict.items() if k != key}
    elif isinstance(input_dict, list):
        return [dict_sweep(element, key) for element in input_dict]
    else:
        return input_dict
def remove_keys(input_dict, keys):
    temp = input_dict
    for rem_key in keys:
        new_dict = dict_sweep(temp, rem_key)
        temp = new_dict
    return new_dict


def price_to_float(price: str) -> float:
    # clean the price string
    trimmer = re.compile(r'[^\d,.]+')
    trimmed = trimmer.sub('', price)
    if ',' not in trimmed and '.' not in trimmed and int (trimmed) > 99: # $995k-$1.035m, a million price is normally presented as 1.3m, 5.6 not 995 (too expensive, never happen in Australia)
        trimmed = trimmed + ",000" # remove k but add ,000
    if len(trimmed) > 5: # house price is expected at least $100,000 in Australia
        pos1 = trimmed.find('.') ##$1.600,000 to $1.650,000
        pos2 = trimmed.find(',')
        if pos1 < pos2:
            trimmed = trimmed.replace('.','')
        trimmed = trimmed.replace(',', '')
        result = float(trimmed)
    else: #1.1m, or 1,1m.
        pos1 = trimmed.find('.')
        pos2 = trimmed.find(',')
        if pos1<pos2 and pos1 != -1:#1.1m, or 1,1m.
            trimmed = trimmed.replace(',', '')
        elif pos2<pos1 and pos2 != -1:
            trimmed = trimmed.replace('.', '')
        trimmed = trimmed.replace(',', '.')
        trimmed = trimmed.rstrip('.')  # Remove the trailing dot, for example  for '6.5.'
        result = float(trimmed) * 1000000
    return result
def cal_expected_price(option):
    phones = re.findall(r'(?:0|61|\+61)[2345678]\d{8}', option)
    for phone in phones:
        option = option.replace(phone, '')  # remove phone from option
    # """
    # (?:0|61|\+61)   Non-capturing group for 0, 61 or +61
    # (?:0|61|\+61)[2345678]\d{8}  following by one digit except 0, 1, 9
    # \d{8}           followed by 8 digits
    # """
    pos = option.rfind('$')
    min = option[:pos]
    max = option[pos:]
    if "$" not in min:  # only one price
        min = max
    min = price_to_float(min)
    max = price_to_float(max)
    expectedPrice = int(float((min + max) / 2))
    return expectedPrice

def is_valid_date(date_str):
    try:
        parser.parse(date_str)
        return True
    except:
        return False
async def cal_expected_prices():# this function is to calculate expectedPrice if
    coll = db.get_collection('PropertyForSale')
    cursor = coll.find({})
    async for cur in cursor:
        try:
            pricingOption = cur['pricing']['pricingOptions']
            expectedPrice = cur['expectedPrice']
            if "$" in pricingOption and (expectedPrice == 0 or expectedPrice is None): # expectedPrice has not been updated
                cleaned_pricingOption = ' '.join([w for w in pricingOption.split() if not is_valid_date(w)]) # remove data time
                expectedPrice = cal_expected_price(cleaned_pricingOption)
                print(pricingOption)
                print(expectedPrice)
                new_document = await do_update_expected_price(coll, int(expectedPrice), cur['_id'])
                print(new_document)
        except:
            print(cur)
            continue

if __name__ == '__main__':
    all_suburb_city = get_json_files()
    output_data = change_key('lga','city',all_suburb_city)
    print(all_suburb_city)
    print("ok")