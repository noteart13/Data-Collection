from fastapi import APIRouter, Depends
from fastapi import Request
from app.api import deps
from app import crud, models, schemas
from operations.property_address import Get_pro_meta, Mapping, Get_html
from motor.core import AgnosticDatabase
from operations.crawl4ai import WebCrawler
from fastapi import FastAPI, HTTPException
environment = "dev"
router = APIRouter()


@router.post('/property/address', response_model=schemas.Prosale)
async def AUS_PROPERTY_META_V1(
        info: Request,
        db: AgnosticDatabase = Depends(deps.get_db),
        current_user: models.User = Depends(deps.get_current_active_user),
        ):
    body = await info.json()
    found_url = Get_html(body['query'])
    if found_url:
        # get slub from found url
        raw_slug = found_url.split('/')[-1]
        pos = raw_slug.rfind('-')
        slug = raw_slug[:pos]
        print("extacted slug from found url", slug)
        new_prosale = await crud.prosale.get_by_slug(db, slug=slug)  # found property in mongodb
        if new_prosale is not None:
        # found property in database
            return new_prosale
        else:
        # scraping from domain
        # to do check other platform if possible to scrape when gotting blocked
            crawler = WebCrawler()
            crawler.warmup()
            result = crawler.run(url=found_url)
            pro_meta = Get_pro_meta(result.html)
            mapping = Mapping(pro_meta)
            final_meta = mapping.mapping()
            if final_meta:
                prosale = final_meta['propertyforsale']
                agency = final_meta['agency']
                agents = final_meta['agents']
                images = final_meta['images']
                schools = final_meta['schools']
                agency_in = schemas.AgencyCreate(**agency)
                agents_in = [schemas.AgentCreate(**agent) for agent in agents]
                images_in = [schemas.ImageCreate(**image) for image in images]
                schools_in = [schemas.SchoolCreate(**school) for school in schools]
                new_agency = await crud.agency.create(db, obj_in=agency_in)
                new_agents = await crud.agent.create_many(db, objs_in=agents_in)
                new_images = await crud.image.create_many(db, objs_in=images_in)
                new_schools = await crud.school.create_many(db, objs_in=schools_in)
                prosale['agencyid'] = new_agency.id
                prosale['agentid'] = [new_agent.id for new_agent in new_agents]
                prosale['schoolid'] = [new_school.id for new_school in new_schools]
                prosale['imageid'] = [new_image.id for new_image in new_images]
                prosale_in = schemas.ProsaleCreate(**prosale)
                new_prosale = await crud.prosale.create(db, obj_in=prosale_in)
                return new_prosale
            else:
                raise HTTPException(
                    status_code=404, detail=f"No prosale found for this address '{body['query']}'"
                )
    else:
            raise HTTPException(
                status_code=404, detail=f"No prosale found for this address '{body['query']}'"
            )