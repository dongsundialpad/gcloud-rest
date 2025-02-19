from typing import Any  # pylint: disable=unused-import
from typing import Dict  # pylint: disable=unused-import
from typing import List  # pylint: disable=unused-import
from typing import Optional  # pylint: disable=unused-import

from gcloud.rest.datastore.constants import MoreResultsType
from gcloud.rest.datastore.constants import ResultType
from gcloud.rest.datastore.entity import EntityResult
from gcloud.rest.datastore.filter import Filter
from gcloud.rest.datastore.property_order import PropertyOrder
from gcloud.rest.datastore.value import Value


class BaseQuery(object):
    json_key = ''
    value_kind = Value

    def __repr__(self):
        # type: () -> str
        return str(self.to_repr())

    @classmethod
    def from_repr(cls, data):
        # type: (Dict[str, Any]) -> BaseQuery
        raise NotImplementedError

    def to_repr(self):
        # type: () -> Dict[str, Any]
        raise NotImplementedError


# https://cloud.google.com/datastore/docs/reference/data/rest/v1/projects/runQuery#Query
class Query(BaseQuery):
    json_key = 'query'

    # TODO: support `projection` and `distinctOn`
    def __init__(self,
                 kind='',            # type: str
                 query_filter=None,  # type: Optional[Filter]
                 order=None,         # type: Optional[List[PropertyOrder]]
                 start_cursor='',    # type: str
                 end_cursor='',      # type: str
                 offset=0,           # type: int
                 limit=0             # type: int
                 ):
        # type: (...) -> None
        self.kind = kind
        self.query_filter = query_filter
        self.orders = order or []
        self.start_cursor = start_cursor
        self.end_cursor = end_cursor
        self.offset = offset
        self.limit = limit

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, Query):
            return False

        return bool(
            self.kind == other.kind
            and self.query_filter == other.query_filter)

    @classmethod
    def from_repr(cls, data):
        # type: (Dict[str, Any]) -> Query
        kind = data['kind'] or ''  # Kind is required
        orders = [PropertyOrder.from_repr(o) for o in data.get('order', [])]
        start_cursor = data.get('startCursor') or ''
        end_cursor = data.get('endCursor') or ''
        offset = int(data.get('offset') or 0)
        limit = int(data.get('limit') or 0)

        filter_ = data.get('filter')
        query_filter = Filter.from_repr(filter_) if filter_ else None

        return cls(kind=kind, query_filter=query_filter, order=orders,
                   start_cursor=start_cursor, end_cursor=end_cursor,
                   offset=offset, limit=limit)

    def to_repr(self):
        # type: () -> Dict[str, Any]
        data = {'kind': [{'name': self.kind}] if self.kind else []}
        if self.query_filter:
            data['filter'] = self.query_filter.to_repr()
        if self.orders:
            data['order'] = [o.to_repr() for o in self.orders]
        if self.start_cursor:
            data['startCursor'] = self.start_cursor
        if self.end_cursor:
            data['endCursor'] = self.end_cursor
        if self.offset:
            data['offset'] = self.offset
        if self.limit:
            data['limit'] = self.limit
        return data


# https://cloud.google.com/datastore/docs/reference/data/rest/v1/projects/runQuery#gqlquery
class GQLQuery(BaseQuery):
    json_key = 'gqlQuery'

    def __init__(self,
                 query_string,             # type: str
                 allow_literals=True,      # type: bool
                 named_bindings=None,      # type: Optional[Dict[str, Any]]
                 positional_bindings=None  # type: Optional[List[Any]]
                 ):
        # type: (...) -> None
        self.query_string = query_string
        self.allow_literals = allow_literals
        self.named_bindings = named_bindings or {}
        self.positional_bindings = positional_bindings or []

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, GQLQuery):
            return False

        return bool(
            self.query_string == other.query_string
            and self.allow_literals == other.allow_literals
            and self.named_bindings == other.named_bindings
            and self.positional_bindings == other.positional_bindings)

    @classmethod
    def from_repr(cls, data):
        # type: (Dict[str, Any]) -> GQLQuery
        allow_literals = data['allowLiterals']
        query_string = data['queryString']
        named_bindings = {k: cls.value_kind.from_repr(v['value'].value)
                          for k, v in data.get('namedBindings', {}).items()}
        positional_bindings = [cls.value_kind.from_repr(v['value'].value)
                               for v in data.get('positionalBindings', [])]
        return cls(query_string, allow_literals=allow_literals,
                   named_bindings=named_bindings,
                   positional_bindings=positional_bindings)

    def to_repr(self):
        # type: () -> Dict[str, Any]
        return {
            'allowLiterals': self.allow_literals,
            'queryString': self.query_string,
            'namedBindings': {k: {'value': self.value_kind(v).to_repr()}
                              for k, v in self.named_bindings.items()},
            'positionalBindings': [{'value': self.value_kind(v).to_repr()}
                                   for v in self.positional_bindings],
        }


class QueryResultBatch(object):
    entity_result_kind = EntityResult

    def __init__(self,
                 end_cursor,  # type: str
                 entity_result_type=ResultType.UNSPECIFIED,  # type: ResultType
                 entity_results=None,  # type: Optional[List[EntityResult]]
                 more_results=MoreResultsType.UNSPECIFIED,
                 skipped_cursor='',   # type: str
                 skipped_results=0,   # type: int
                 snapshot_version=''  # type: str
                 ):
        # type: (...) -> None
        self.end_cursor = end_cursor

        self.entity_result_type = entity_result_type
        self.entity_results = entity_results or []
        self.more_results = more_results
        self.skipped_cursor = skipped_cursor
        self.skipped_results = skipped_results
        self.snapshot_version = snapshot_version

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, QueryResultBatch):
            return False

        return bool(self.end_cursor == other.end_cursor
                    and self.entity_result_type == other.entity_result_type
                    and self.entity_results == other.entity_results
                    and self.more_results == other.more_results
                    and self.skipped_cursor == other.skipped_cursor
                    and self.skipped_results == other.skipped_results
                    and self.snapshot_version == other.snapshot_version)

    def __repr__(self):
        # type: () -> str
        return str(self.to_repr())

    @classmethod
    def from_repr(cls, data):
        # type: (Dict[str, Any]) -> QueryResultBatch
        end_cursor = data['endCursor']
        entity_result_type = ResultType(data['entityResultType'])
        entity_results = [cls.entity_result_kind.from_repr(er)
                          for er in data.get('entityResults', [])]
        more_results = MoreResultsType(data['moreResults'])
        skipped_cursor = data.get('skippedCursor', '')
        skipped_results = data.get('skippedResults', 0)
        snapshot_version = data.get('snapshotVersion', '')
        return cls(end_cursor, entity_result_type=entity_result_type,
                   entity_results=entity_results, more_results=more_results,
                   skipped_cursor=skipped_cursor,
                   skipped_results=skipped_results,
                   snapshot_version=snapshot_version)

    def to_repr(self):
        # type: () -> Dict[str, Any]
        data = {
            'endCursor': self.end_cursor,
            'entityResults': [er.to_repr() for er in self.entity_results],
            'entityResultType': self.entity_result_type.value,
            'moreResults': self.more_results.value,
            'skippedResults': self.skipped_results,
        }
        if self.skipped_cursor:
            data['skippedCursor'] = self.skipped_cursor
        if self.snapshot_version:
            data['snapshotVersion'] = self.snapshot_version

        return data
