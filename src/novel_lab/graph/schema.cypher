// novel-lab 知识图谱 schema
// 节点类型：Book, Chapter, Character, Location, Event, Theme, Trope, Quote
// 关系类型：CONTAINS_CHAPTER, APPEARS_IN, SET_IN, RELATIONSHIP_WITH, CAUSES,
//          FORESHADOWS, EVOLVES_TO, SAYS, USES_TROPE, BELONGS_TO_LINE

CREATE CONSTRAINT book_id_unique IF NOT EXISTS
  FOR (b:Book) REQUIRE b.id IS UNIQUE;

CREATE CONSTRAINT chapter_id_unique IF NOT EXISTS
  FOR (c:Chapter) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT character_id_unique IF NOT EXISTS
  FOR (c:Character) REQUIRE c.id IS UNIQUE;

CREATE CONSTRAINT location_id_unique IF NOT EXISTS
  FOR (l:Location) REQUIRE l.id IS UNIQUE;

CREATE CONSTRAINT event_id_unique IF NOT EXISTS
  FOR (e:Event) REQUIRE e.id IS UNIQUE;

CREATE CONSTRAINT trope_id_unique IF NOT EXISTS
  FOR (t:Trope) REQUIRE t.id IS UNIQUE;

CREATE CONSTRAINT quote_id_unique IF NOT EXISTS
  FOR (q:Quote) REQUIRE q.id IS UNIQUE;

CREATE INDEX chapter_book_idx IF NOT EXISTS FOR (c:Chapter) ON (c.book_id);
CREATE INDEX character_book_idx IF NOT EXISTS FOR (c:Character) ON (c.book_id);
