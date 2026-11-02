from sqlalchemy.orm import Session

from app.domain.sample_image import SampleImage


def save_sample_image(session: Session, image: SampleImage) -> SampleImage:
    session.add(image)
    session.commit()
    session.refresh(image)
    return image
