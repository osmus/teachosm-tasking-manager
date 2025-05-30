import geojson
import io
from flask import send_file
from flask_restful import Resource, current_app, request
from schematics.exceptions import DataError
from distutils.util import strtobool
from backend.models.dtos.project_dto import (
    DraftProjectDTO,
    ProjectDTO,
    ProjectSearchDTO,
    ProjectSearchBBoxDTO,
)
from backend.services.project_search_service import (
    ProjectSearchService,
    ProjectSearchServiceError,
    BBoxTooBigError,
)
from backend.services.project_service import (
    ProjectService,
    ProjectServiceError,
    NotFound,
)
from backend.services.users.user_service import UserService
from backend.services.organisation_service import OrganisationService
from backend.services.users.authentication_service import token_auth
from backend.services.project_admin_service import (
    ProjectAdminService,
    ProjectAdminServiceError,
    InvalidGeoJson,
    InvalidData,
)
from backend.services.recommendation_service import ProjectRecommendationService


class ProjectsRestAPI(Resource):
    @token_auth.login_required(optional=True)
    def get(self, project_id):
        """
        Get a specified project including it's area
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: false
              type: string
              default: Token sessionTokenHere==
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
            - in: query
              name: as_file
              type: boolean
              description: Set to true if file download is preferred
              default: False
            - in: query
              name: abbreviated
              type: boolean
              description: Set to true if only state information is desired
              default: False
        responses:
            200:
                description: Project found
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        try:
            authenticated_user_id = token_auth.current_user()
            as_file = bool(
                strtobool(request.args.get("as_file"))
                if request.args.get("as_file")
                else False
            )
            abbreviated = bool(
                strtobool(request.args.get("abbreviated"))
                if request.args.get("abbreviated")
                else False
            )
            project_dto = ProjectService.get_project_dto_for_mapper(
                project_id,
                authenticated_user_id,
                request.environ.get("HTTP_ACCEPT_LANGUAGE"),
                abbreviated,
            )

            if project_dto:
                project_dto = project_dto.to_primitive()
                if as_file:
                    return send_file(
                        io.BytesIO(geojson.dumps(project_dto).encode("utf-8")),
                        mimetype="application/json",
                        as_attachment=True,
                        download_name=f"project_{str(project_id)}.json",
                    )

                return project_dto, 200
            else:
                return {
                    "Error": "User not permitted: Private Project",
                    "SubCode": "PrivateProject",
                }, 403
        except ProjectServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 403
        finally:
            # this will try to unlock tasks that have been locked too long
            try:
                ProjectService.auto_unlock_tasks(project_id)
            except Exception as e:
                current_app.logger.critical(str(e))

    @token_auth.login_required
    def post(self):
        """
        Creates a tasking-manager project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - in: body
              name: body
              required: true
              description: JSON object for creating draft project
              schema:
                properties:
                    cloneFromProjectId:
                        type: int
                        default: 1
                        description: Specify this value if you want to clone a project, otherwise avoid information
                    projectName:
                        type: string
                        default: HOT Project
                    database:
                        type: string
                        default: OSM
                    areaOfInterest:
                        schema:
                            properties:
                                type:
                                    type: string
                                    default: FeatureCollection
                                features:
                                    type: array
                                    items:
                                        schema:
                                            $ref: "#/definitions/GeoJsonFeature"
                        tasks:
                            schema:
                                properties:
                                    type:
                                        type: string
                                        default: FeatureCollection
                                    features:
                                        type: array
                                        items:
                                            schema:
                                                $ref: "#/definitions/GeoJsonFeature"
                        arbitraryTasks:
                            type: boolean
                            default: false
        responses:
            201:
                description: Draft project created successfully
            400:
                description: Client Error - Invalid Request
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            500:
                description: Internal Server Error
        """
        try:
            draft_project_dto = DraftProjectDTO(request.get_json())
            draft_project_dto.user_id = token_auth.current_user()
            draft_project_dto.validate()
        except DataError as e:
            current_app.logger.error(f"error validating request: {str(e)}")
            return {"Error": "Unable to create project", "SubCode": "InvalidData"}, 400

        try:
            draft_project_id = ProjectAdminService.create_draft_project(
                draft_project_dto
            )
            return {"projectId": draft_project_id}, 201
        except ProjectAdminServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 403
        except (InvalidGeoJson, InvalidData) as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 400

    @token_auth.login_required
    def head(self, project_id):
        """
        Retrieves a Tasking-Manager project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
        responses:
            200:
                description: Project found
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        try:
            ProjectAdminService.is_user_action_permitted_on_project(
                token_auth.current_user(), project_id
            )
        except ValueError:
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403

        project_dto = ProjectAdminService.get_project_dto_for_admin(project_id)
        return project_dto.to_primitive(), 200

    @token_auth.login_required
    def patch(self, project_id):
        """
        Updates a Tasking-Manager project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
            - in: body
              name: body
              required: true
              description: JSON object for updating an existing project
              schema:
                properties:
                    projectDatabase:
                        type: string
                        default: 
                    projectStatus:
                        type: string
                        default: DRAFT
                    projectPriority:
                        type: string
                        default: MEDIUM
                    defaultLocale:
                        type: string
                        default: en
                    difficulty:
                        type: string
                        default: EASY
                    database:
                        type: string
                        default: OSM
                    validation_permission:
                        type: string
                        default: ANY
                    mapping_permission:
                        type: string
                        default: ANY
                    private:
                        type: boolean
                        default: false
                    changesetComment:
                        type: string
                        default: hotosm-project-1
                    dueDate:
                        type: date
                        default: "2017-04-11T12:38:49"
                    imagery:
                        type: string
                        default: http//www.bing.com/maps/
                    josmPreset:
                        type: string
                        default: josm preset goes here
                    mappingTypes:
                        type: array
                        items:
                            type: string
                        default: [BUILDINGS, ROADS]
                    mappingEditors:
                        type: array
                        items:
                            type: string
                        default: [ID, JOSM, POTLATCH_2, FIELD_PAPERS]
                    validationEditors:
                        type: array
                        items:
                            type: string
                        default: [ID, JOSM, POTLATCH_2, FIELD_PAPERS]
                    campaign:
                        type: string
                        default: malaria
                    organisation:
                        type: integer
                        default: 1
                    countryTag:
                          type: array
                          items:
                              type: string
                          default: []
                    licenseId:
                        type: integer
                        default: 1
                        description: Id of imagery license associated with the project
                    allowedUsernames:
                        type: array
                        items:
                            type: string
                        default: ["Iain Hunter", LindaA1]
                    priorityAreas:
                        type: array
                        items:
                            schema:
                                $ref: "#/definitions/GeoJsonPolygon"
                    projectInfoLocales:
                        type: array
                        items:
                            schema:
                                $ref: "#/definitions/ProjectInfo"
                    taskCreationMode:
                        type: integer
                        default: GRID
        responses:
            200:
                description: Project updated
            400:
                description: Client Error - Invalid Request
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        authenticated_user_id = token_auth.current_user()
        if not ProjectAdminService.is_user_action_permitted_on_project(
            authenticated_user_id, project_id
        ):
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403
        try:
            project_dto = ProjectDTO(request.get_json())
            project_dto.project_id = project_id
            project_dto.validate()
        except DataError as e:
            current_app.logger.error(f"Error validating request: {str(e)}")
            return {"Error": "Unable to update project", "SubCode": "InvalidData"}, 400

        try:
            ProjectAdminService.update_project(project_dto, authenticated_user_id)
            return {"Status": "Updated"}, 200
        except InvalidGeoJson as e:
            return {"Invalid GeoJson": str(e)}, 400
        except ProjectAdminServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 403

    @token_auth.login_required
    def delete(self, project_id):
        """
        Deletes a Tasking-Manager project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
        responses:
            200:
                description: Project deleted
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        try:
            authenticated_user_id = token_auth.current_user()
            if not ProjectAdminService.is_user_action_permitted_on_project(
                authenticated_user_id, project_id
            ):
                raise ValueError()
        except ValueError:
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403

        try:
            ProjectAdminService.delete_project(project_id, authenticated_user_id)
            return {"Success": "Project deleted"}, 200
        except ProjectAdminServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 403


class ProjectSearchBase(Resource):
    @token_auth.login_required(optional=True)
    def setup_search_dto(self) -> ProjectSearchDTO:
        search_dto = ProjectSearchDTO()
        search_dto.preferred_locale = request.environ.get("HTTP_ACCEPT_LANGUAGE")
        search_dto.difficulty = request.args.get("difficulty")
        search_dto.database = request.args.get("database")
        search_dto.action = request.args.get("action")
        search_dto.organisation_name = request.args.get("organisationName")
        search_dto.organisation_id = request.args.get("organisationId")
        search_dto.team_id = request.args.get("teamId")
        search_dto.campaign = request.args.get("campaign")
        search_dto.order_by = request.args.get("orderBy", "priority")
        search_dto.country = request.args.get("country")
        search_dto.order_by_type = request.args.get("orderByType", "ASC")
        search_dto.page = (
            int(request.args.get("page")) if request.args.get("page") else 1
        )
        search_dto.text_search = request.args.get("textSearch")
        search_dto.omit_map_results = strtobool(
            request.args.get("omitMapResults", "false")
        )
        search_dto.last_updated_gte = request.args.get("lastUpdatedFrom")
        search_dto.last_updated_lte = request.args.get("lastUpdatedTo")
        search_dto.created_gte = request.args.get("createdFrom")
        search_dto.created_lte = request.args.get("createdTo")

        # See https://github.com/hotosm/tasking-manager/pull/922 for more info
        try:
            authenticated_user_id = token_auth.current_user()
            if request.args.get("createdByMe") == "true":
                search_dto.created_by = authenticated_user_id

            if request.args.get("mappedByMe") == "true":
                search_dto.mapped_by = authenticated_user_id

            if request.args.get("favoritedByMe") == "true":
                search_dto.favorited_by = authenticated_user_id

            if request.args.get("managedByMe") == "true":
                search_dto.managed_by = authenticated_user_id
            if request.args.get("basedOnMyInterests") == "true":
                search_dto.based_on_user_interests = authenticated_user_id

        except Exception:
            pass

        mapping_types_str = request.args.get("mappingTypes")
        if mapping_types_str:
            search_dto.mapping_types = map(
                str, mapping_types_str.split(",")
            )  # Extract list from string
        search_dto.mapping_types_exact = strtobool(
            request.args.get("mappingTypesExact", "false")
        )
        project_statuses_str = request.args.get("projectStatuses")
        if project_statuses_str:
            search_dto.project_statuses = map(str, project_statuses_str.split(","))
        interests_str = request.args.get("interests")
        if interests_str:
            search_dto.interests = map(int, interests_str.split(","))
        search_dto.validate()

        return search_dto


class ProjectsAllAPI(ProjectSearchBase):
    @token_auth.login_required(optional=True)
    def get(self):
        """
        List and search for projects
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              type: string
              default: Token sessionTokenHere==
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
            - in: query
              name: difficulty
              type: string
            - in: query
              name: database
              type: string
            - in: query
              name: orderBy
              type: string
              default: priority
              enum: [id,difficulty,priority,status,last_updated,due_date]
            - in: query
              name: orderByType
              type: string
              default: ASC
              enum: [ASC, DESC]
            - in: query
              name: mappingTypes
              type: string
            - in: query
              name: mappingTypesExact
              type: boolean
              default: false
              description: if true, limits projects to match the exact mapping types requested
            - in: query
              name: organisationName
              description: Organisation name to search for
              type: string
            - in: query
              name: organisationId
              description: Organisation ID to search for
              type: integer
            - in: query
              name: campaign
              description: Campaign name to search for
              type: string
            - in: query
              name: page
              description: Page of results user requested
              type: integer
              default: 1
            - in: query
              name: textSearch
              description: Text to search
              type: string
            - in: query
              name: country
              description: Project country
              type: string
            - in: query
              name: action
              description: Filter projects by possible actions
              enum: [map, validate, any]
              type: string
            - in: query
              name: projectStatuses
              description: Authenticated PMs can search for archived or draft statuses
              type: string
            - in: query
              name: lastUpdatedFrom
              description: Filter projects whose last update date is equal or greater than a date
              type: string
            - in: query
              name: lastUpdatedTo
              description: Filter projects whose last update date is equal or lower than a date
              type: string
            - in: query
              name: createdFrom
              description: Filter projects whose creation date is equal or greater than a date
              type: string
            - in: query
              name: createdTo
              description: Filter projects whose creation date is equal or lower than a date
              type: string
            - in: query
              name: interests
              type: string
              description: Filter by interest on project
              default: null
            - in: query
              name: createdByMe
              description: Limit to projects created by the authenticated user
              type: boolean
              default: false
            - in: query
              name: mappedByMe
              description: Limit to projects mapped/validated by the authenticated user
              type: boolean
              default: false
            - in: query
              name: favoritedByMe
              description: Limit to projects favorited by the authenticated user
              type: boolean
              default: false
            - in: query
              name: managedByMe
              description:
                Limit to projects that can be managed by the authenticated user,
                excluding the ones created by them
              type: boolean
              default: false
            - in: query
              name: basedOnMyInterests
              type: boolean
              description: Filter projects based on user interests
              default: false
            - in: query
              name: teamId
              type: string
              description: Filter by team on project
              default: null
              name: omitMapResults
              type: boolean
              description: If true, it will not return the project centroid's geometries.
              default: false
        responses:
            200:
                description: Projects found
            404:
                description: No projects found
            500:
                description: Internal Server Error
        """
        try:
            user = None
            user_id = token_auth.current_user()
            if user_id:
                user = UserService.get_user_by_id(user_id)
            search_dto = self.setup_search_dto()
            results_dto = ProjectSearchService.search_projects(search_dto, user)
            return results_dto.to_primitive(), 200
        except NotFound:
            return {"mapResults": {}, "results": []}, 200
        except (KeyError, ValueError) as e:
            error_msg = f"Projects GET - {str(e)}"
            return {"Error": error_msg}, 400


class ProjectsQueriesBboxAPI(Resource):
    @token_auth.login_required
    def get(self):
        """
        List and search projects by bounding box
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              default: en
            - in: query
              name: bbox
              description: comma separated list xmin, ymin, xmax, ymax
              type: string
              required: true
              default: 34.404,-1.034, 34.717,-0.624
            - in: query
              name: srid
              description: srid of bbox coords
              type: integer
              default: 4326
            - in: query
              name: createdByMe
              description: limit to projects created by authenticated user
              type: boolean
              required: true
              default: false

        responses:
            200:
                description: ok
            400:
                description: Client Error - Invalid Request
            403:
                description: Forbidden
            500:
                description: Internal Server Error
        """
        authenticated_user_id = token_auth.current_user()
        orgs_dto = OrganisationService.get_organisations_managed_by_user_as_dto(
            authenticated_user_id
        )
        if len(orgs_dto.organisations) < 1:
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403

        try:
            search_dto = ProjectSearchBBoxDTO()
            search_dto.bbox = map(float, request.args.get("bbox").split(","))
            search_dto.input_srid = request.args.get("srid")
            search_dto.preferred_locale = request.environ.get("HTTP_ACCEPT_LANGUAGE")
            created_by_me = (
                strtobool(request.args.get("createdByMe"))
                if request.args.get("createdByMe")
                else False
            )
            if created_by_me:
                search_dto.project_author = authenticated_user_id
            search_dto.validate()
        except Exception as e:
            current_app.logger.error(f"Error validating request: {str(e)}")
            return {
                "Error": f"Error validating request: {str(e)}",
                "SubCode": "InvalidData",
            }, 400
        try:
            geojson = ProjectSearchService.get_projects_geojson(search_dto)
            return geojson, 200
        except BBoxTooBigError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 400
        except ProjectSearchServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 400


class ProjectsQueriesOwnerAPI(ProjectSearchBase):
    @token_auth.login_required
    def get(self):
        """
        Get all projects for logged in admin
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
        responses:
            200:
                description: All mapped tasks validated
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            404:
                description: Admin has no projects
            500:
                description: Internal Server Error
        """
        authenticated_user_id = token_auth.current_user()
        orgs_dto = OrganisationService.get_organisations_managed_by_user_as_dto(
            authenticated_user_id
        )
        if len(orgs_dto.organisations) < 1:
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403

        search_dto = self.setup_search_dto()
        admin_projects = ProjectAdminService.get_projects_for_admin(
            authenticated_user_id,
            request.environ.get("HTTP_ACCEPT_LANGUAGE"),
            search_dto,
        )
        return admin_projects.to_primitive(), 200


class ProjectsQueriesTouchedAPI(Resource):
    def get(self, username):
        """
        Gets projects user has mapped
        ---
        tags:
          - projects
        produces:
          - application/json
        parameters:
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
            - name: username
              in: path
              description: The users username
              required: true
              type: string
              default: Thinkwhere
        responses:
            200:
                description: Mapped projects found
            404:
                description: User not found
            500:
                description: Internal Server Error
        """
        locale = (
            request.environ.get("HTTP_ACCEPT_LANGUAGE")
            if request.environ.get("HTTP_ACCEPT_LANGUAGE")
            else "en"
        )
        user_dto = UserService.get_mapped_projects(username, locale)
        return user_dto.to_primitive(), 200


class ProjectsQueriesSummaryAPI(Resource):
    def get(self, project_id: int):
        """
        Gets project summary
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
            - name: project_id
              in: path
              description: The ID of the project
              required: true
              type: integer
              default: 1
        responses:
            200:
                description: Project Summary
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        preferred_locale = request.environ.get("HTTP_ACCEPT_LANGUAGE")
        summary = ProjectService.get_project_summary(project_id, preferred_locale)
        return summary.to_primitive(), 200


class ProjectsQueriesNoGeometriesAPI(Resource):
    def get(self, project_id):
        """
        Get HOT Project for mapping
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Accept-Language
              description: Language user is requesting
              type: string
              required: true
              default: en
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
            - in: query
              name: as_file
              type: boolean
              description: Set to true if file download is preferred
              default: False
        responses:
            200:
                description: Project found
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        try:
            as_file = (
                strtobool(request.args.get("as_file"))
                if request.args.get("as_file")
                else False
            )
            locale = request.environ.get("HTTP_ACCEPT_LANGUAGE")
            project_dto = ProjectService.get_project_dto_for_mapper(
                project_id, None, locale, True
            )
            project_dto = project_dto.to_primitive()

            if as_file:
                return send_file(
                    io.BytesIO(geojson.dumps(project_dto).encode("utf-8")),
                    mimetype="application/json",
                    as_attachment=True,
                    download_name=f"project_{str(project_id)}.json",
                )

            return project_dto, 200
        except ProjectServiceError as e:
            return {"Error": str(e).split("-")[1], "SubCode": str(e).split("-")[0]}, 403
        finally:
            # this will try to unlock tasks that have been locked too long
            try:
                ProjectService.auto_unlock_tasks(project_id)
            except Exception as e:
                current_app.logger.critical(str(e))


class ProjectsQueriesNoTasksAPI(Resource):
    @token_auth.login_required
    def get(self, project_id):
        """
        Retrieves a Tasking-Manager project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: true
              type: string
              default: Token sessionTokenHere==
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
        responses:
            200:
                description: Project found
            401:
                description: Unauthorized - Invalid credentials
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        if not ProjectAdminService.is_user_action_permitted_on_project(
            token_auth.current_user(), project_id
        ):
            return {
                "Error": "User is not a manager of the project",
                "SubCode": "UserPermissionError",
            }, 403

        project_dto = ProjectAdminService.get_project_dto_for_admin(project_id)
        return project_dto.to_primitive(), 200


class ProjectsQueriesAoiAPI(Resource):
    def get(self, project_id):
        """
        Get AOI of Project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
            - in: query
              name: as_file
              type: boolean
              description: Set to false if file download not preferred
              default: True
        responses:
            200:
                description: Project found
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        as_file = (
            strtobool(request.args.get("as_file"))
            if request.args.get("as_file")
            else True
        )

        project_aoi = ProjectService.get_project_aoi(project_id)

        if as_file:
            return send_file(
                io.BytesIO(geojson.dumps(project_aoi).encode("utf-8")),
                mimetype="application/json",
                as_attachment=True,
                download_name=f"{str(project_id)}.geojson",
            )

        return project_aoi, 200


class ProjectsQueriesPriorityAreasAPI(Resource):
    def get(self, project_id):
        """
        Get Priority Areas of a project
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - name: project_id
              in: path
              description: Unique project ID
              required: true
              type: integer
              default: 1
        responses:
            200:
                description: Project found
            403:
                description: Forbidden
            404:
                description: Project not found
            500:
                description: Internal Server Error
        """
        try:
            priority_areas = ProjectService.get_project_priority_areas(project_id)
            return priority_areas, 200
        except ProjectServiceError:
            return {"Error": "Unable to fetch project"}, 403


class ProjectsQueriesFeaturedAPI(Resource):
    def get(self):
        """
        Get featured projects
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: false
              type: string
              default: Token sessionTokenHere==
        responses:
            200:
                description: Featured projects
            500:
                description: Internal Server Error
        """
        preferred_locale = request.environ.get("HTTP_ACCEPT_LANGUAGE")
        projects_dto = ProjectService.get_featured_projects(preferred_locale)
        return projects_dto.to_primitive(), 200


class ProjectQueriesSimilarProjectsAPI(Resource):
    @token_auth.login_required(optional=True)
    def get(self, project_id):
        """
        Get similar projects
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: false
              type: string
              default: Token sessionTokenHere==
            - name: project_id
              in: path
              description: Project ID to get similar projects for
              required: true
              type: integer
              default: 1
            - in: query
              name: limit
              type: integer
              description: Number of similar projects to return
              default: 4
        responses:
            200:
                description: Similar projects
            404:
                description: Project not found or project is not published
            500:
                description: Internal Server Error
        """
        authenticated_user_id = (
            token_auth.current_user() if token_auth.current_user() else None
        )
        limit = int(request.args.get("limit", 4))
        preferred_locale = request.environ.get("HTTP_ACCEPT_LANGUAGE", "en")
        projects_dto = ProjectRecommendationService.get_similar_projects(
            project_id, authenticated_user_id, preferred_locale, limit
        )
        return projects_dto.to_primitive(), 200


class ProjectQueriesActiveProjectsAPI(Resource):
    @token_auth.login_required(optional=True)
    def get(self):
        """
        Get active projects
        ---
        tags:
            - projects
        produces:
            - application/json
        parameters:
            - in: header
              name: Authorization
              description: Base64 encoded session token
              required: false
              type: string
              default: Token sessionTokenHere==
            - name: interval
              in: path
              description: Time interval in hours to get active project
              required: false
              type: integer
              default: 24
        responses:
            200:
                description: Active projects geojson
            404:
                description: Project not found or project is not published
            500:
                description: Internal Server Error
        """
        interval = request.args.get("interval", "24")
        if not interval.isdigit():
            return {
                "Error": "Interval must be a number greater than 0 and less than or equal to 24"
            }, 400
        interval = int(interval)
        if interval <= 0 or interval > 24:
            return {
                "Error": "Interval must be a number greater than 0 and less than or equal to 24"
            }, 400
        projects_dto = ProjectService.get_active_projects(interval)
        return projects_dto, 200
