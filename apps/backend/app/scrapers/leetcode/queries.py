"""All LeetCode GraphQL queries. Structures are copied from the tested scripts.

LeetCode's GraphQL API is undocumented and may change; keep every query here so
it can be updated in one place.
"""

# Public
PROFILE_QUERY = """
query userProfile($username: String!) {
    matchedUser(username: $username) {
        username
        profile {
            realName
            userAvatar
            countryName
            ranking
            reputation
            aboutMe
        }
        submitStats: submitStatsGlobal {
            acSubmissionNum {
                difficulty
                count
                submissions
            }
            totalSubmissionNum {
                difficulty
                count
                submissions
            }
        }
        languageProblemCount {
            languageName
            problemsSolved
        }
        tagProblemCounts {
            advanced {
                tagName
                tagSlug
                problemsSolved
            }
            intermediate {
                tagName
                tagSlug
                problemsSolved
            }
            fundamental {
                tagName
                tagSlug
                problemsSolved
            }
        }
    }
    allQuestionsCount {
        difficulty
        count
    }
}
"""

# Public
RECENT_ACCEPTED_QUERY = """
query recentAccepted($username: String!, $limit: Int!) {
    recentAcSubmissionList(username: $username, limit: $limit) {
        id
        title
        titleSlug
        timestamp
    }
}
"""

# Public
RECENT_SUBMISSIONS_QUERY = """
query recentSubmissions($username: String!, $limit: Int!) {
    recentSubmissionList(username: $username, limit: $limit) {
        id
        title
        titleSlug
        timestamp
        statusDisplay
        lang
    }
}
"""

# Public
CALENDAR_QUERY = """
query userCalendar($username: String!, $year: Int!) {
    matchedUser(username: $username) {
        userCalendar(year: $year) {
            activeYears
            streak
            totalActiveDays
            submissionCalendar
        }
    }
}
"""

# Authenticated. Returns the progress of the SIGNED-IN user: it takes no username.
SOLVED_PROBLEMS_QUERY = """
query userProgressQuestionList($filters: UserProgressQuestionListInput) {
    userProgressQuestionList(filters: $filters) {
        totalNum
        questions {
            frontendId
            title
            titleSlug
            difficulty
            lastSubmittedAt
            topicTags {
                name
            }
        }
    }
}
"""

# Authenticated. Used to confirm whose session this is (see SOLVED_PROBLEMS_QUERY).
# NOTE: not part of the original tested scripts; verify with the live test.
USER_STATUS_QUERY = """
query globalData {
    userStatus {
        isSignedIn
        username
    }
}
"""